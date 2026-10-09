"""Text embedding backends.

Retrieval quality is the floor under every scoring term, but embeddings are
also the cheapest part of the pipeline to get wrong expensively: the gateway
charges per call, rate-limits under load, and — worst of all — falls back
between providers mid-run, which silently mixes two incompatible vector
spaces. A local model removes all three problems at once. It costs a few
seconds of CPU for a whole archive and can be re-run as often as an
experiment needs.

Both backends satisfy `Embedder`, so nothing downstream knows which is in
play. Two properties that a naive wrapper would miss:

1. *Identity.* `name` is written to the store next to every vector. Two
   different 384-dimension models produce vectors that mix without any
   dimension check catching it, which is exactly the failure that already
   cost one run.
2. *Asymmetry.* Retrieval models in the BGE/E5 family are trained with a
   prefix on the query side only. Embedding a question the same way as a
   document measurably degrades ranking, so `kind` is part of the protocol.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

Kind = Literal["document", "query"]


class EmbeddingError(RuntimeError):
    """A backend could not produce vectors."""


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into comparable vectors."""

    @property
    def name(self) -> str:
        """Stable identity of the vector space, recorded alongside vectors."""

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> list[list[float]]: ...


# ---------------------------------------------------------------- local models


@dataclass(frozen=True)
class LocalModelSpec:
    repo: str
    pooling: Literal["cls", "mean"]
    dim: int
    # Applied to queries only. The BGE family is trained with this exact
    # string; changing the wording costs a little retrieval accuracy.
    query_prefix: str = ""
    max_tokens: int = 512


_BGE_QUERY = "Represent this sentence for searching relevant passages: "

LOCAL_MODELS: dict[str, LocalModelSpec] = {
    # Default. Already present in the local HuggingFace cache, and the same
    # family as the `bge-large-en-v1.5` the gateway run was pinned to, so
    # scores stay broadly comparable across the two backends.
    "bge-base": LocalModelSpec("BAAI/bge-base-en-v1.5", "cls", 768, _BGE_QUERY),
    # Four times smaller and roughly three times faster, at a modest cost in
    # retrieval accuracy. Worth it while iterating on prompts, not for a run
    # whose numbers get written down.
    "minilm": LocalModelSpec("sentence-transformers/all-MiniLM-L6-v2", "mean", 384, max_tokens=256),
    "bge-small": LocalModelSpec("BAAI/bge-small-en-v1.5", "cls", 384, _BGE_QUERY),
    "bge-large": LocalModelSpec("BAAI/bge-large-en-v1.5", "cls", 1024, _BGE_QUERY),
    "mxbai": LocalModelSpec("mixedbread-ai/mxbai-embed-large-v1", "cls", 1024, _BGE_QUERY),
}

DEFAULT_LOCAL_MODEL = "bge-base"


def resolve_local_model(name: str) -> LocalModelSpec:
    """Accept either a registry alias or a raw HuggingFace repo id."""
    if name in LOCAL_MODELS:
        return LOCAL_MODELS[name]
    if "/" in name:
        # An unregistered repo. Mean pooling with no prefix is the safe
        # default: it is what most sentence encoders expect, and a wrong
        # prefix hurts more than a missing one.
        return LocalModelSpec(name, "mean", dim=0)
    raise EmbeddingError(
        f"unknown local embedding model {name!r}. "
        f"Use one of {', '.join(sorted(LOCAL_MODELS))}, or a full HuggingFace repo id."
    )


class LocalEmbedder:
    """HuggingFace transformer encoder running on this machine.

    Torch and transformers are imported on first use, not at construction, so
    that merely building a config stays instant and the test suite does not
    pay three seconds of torch import to check unrelated behaviour.
    """

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_MODEL,
        *,
        device: str | None = None,
        batch_size: int = 16,
        cache_size: int = 8192,
    ) -> None:
        self.spec = resolve_local_model(model)
        self.batch_size = batch_size
        self._device = device
        self._tokenizer = None
        self._model = None
        # Hot cache. `prepare_context` and the editor re-embed the same
        # required-context strings on every rescore; this makes that free.
        self._cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._cache_size = cache_size

    @property
    def name(self) -> str:
        return f"local:{self.spec.repo}"

    # -- model loading ----------------------------------------------------

    def _pick_device(self) -> str:
        import torch

        if self._device:
            return self._device
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _load(self):
        if self._model is not None:
            return self._tokenizer, self._model
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise EmbeddingError(
                "local embeddings need torch and transformers:\n"
                "  uv sync --extra local\n"
                "or set MASHUP_EMBED_BACKEND=gateway to use the fleet gateway instead."
            ) from exc

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(self.spec.repo)
            model = AutoModel.from_pretrained(self.spec.repo)
        except OSError as exc:
            raise EmbeddingError(
                f"could not load {self.spec.repo!r}. It is not in the local "
                f"HuggingFace cache and could not be downloaded: {exc}"
            ) from exc
        model.eval()
        model.to(self._pick_device())
        self._model = model
        self._torch = torch
        return self._tokenizer, self._model

    # -- embedding --------------------------------------------------------

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> list[list[float]]:
        texts = list(texts)
        if not texts:
            return []

        out: list[list[float]] = [[] for _ in texts]
        misses = [i for i, t in enumerate(texts) if (kind, t) not in self._cache]
        for i, text in enumerate(texts):
            hit = self._cache.get((kind, text))
            if hit is not None:
                self._cache.move_to_end((kind, text))
                out[i] = hit

        if misses:
            prefix = self.spec.query_prefix if kind == "query" else ""
            # Encode longest-first so each batch pads to a length close to its
            # own longest member rather than to the archive's worst case.
            order = sorted(misses, key=lambda i: -len(texts[i]))
            for start in range(0, len(order), self.batch_size):
                batch = order[start : start + self.batch_size]
                vectors = self._encode([prefix + texts[i] for i in batch])
                for i, vec in zip(batch, vectors, strict=True):
                    out[i] = vec
                    self._remember((kind, texts[i]), vec)
        return out

    def _encode(self, texts: list[str]) -> list[list[float]]:
        tokenizer, model = self._load()
        torch = self._torch
        batch = tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.spec.max_tokens,
            return_tensors="pt",
        ).to(model.device)
        with torch.inference_mode():
            hidden = model(**batch).last_hidden_state
            pooled = self._pool(hidden, batch["attention_mask"])
            # Normalise here so every consumer can treat a dot product as
            # cosine similarity without re-checking.
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=-1)
        return pooled.float().cpu().tolist()

    def _pool(self, hidden, mask):
        if self.spec.pooling == "cls":
            return hidden[:, 0]
        weights = mask.unsqueeze(-1).to(hidden.dtype)
        return (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp(min=1e-9)

    def _remember(self, key: tuple[str, str], vec: list[float]) -> None:
        self._cache[key] = vec
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


# -------------------------------------------------------------- gateway model


class GatewayEmbedder:
    """The fleet free-ai gateway. Kept for parity checks against local runs."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway

    @property
    def name(self) -> str:
        gw = self.gateway
        served = getattr(gw, "embed_model_used", None) or gw.config.embed_model
        return f"gateway:{served}"

    def embed(self, texts: Sequence[str], *, kind: Kind = "document") -> list[list[float]]:
        # No asymmetry: the OpenAI-compatible embeddings endpoint exposes no
        # way to say "this one is a query".
        del kind
        return self.gateway.embed(list(texts))


def make_embedder(cfg, *, gateway=None) -> Embedder:
    """Build the embedder named by config."""
    if cfg.embed_backend == "local":
        return LocalEmbedder(cfg.local_embed_model)
    if cfg.embed_backend == "gateway":
        if gateway is None:
            from mashup.gateway import Gateway

            gateway = Gateway(cfg)
        return GatewayEmbedder(gateway)
    raise EmbeddingError(
        f"unknown embedding backend {cfg.embed_backend!r} (expected 'local' or 'gateway')"
    )
