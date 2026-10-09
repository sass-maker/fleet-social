"""Chat backends for segment enrichment.

Enrichment is the last stage that needed the network, and it was the worst one
to depend on: 727 segments took four gateway passes to drain past routing
failures and rate limits. An instruction-tuned 4B model on Apple silicon does
the same work in about sixteen minutes, offline, for nothing, and — unlike a
gateway that picks whichever provider is cheapest this minute — the same model
every time.

`Gateway` already satisfies `ChatModel`; this module adds the local one and
the batching contract they share. That contract is `chat_json_many` rather
than one call at a time, because the two backends parallelise in completely
different ways: the gateway wants concurrent HTTP requests, mlx wants one
batched forward pass. Handing both a list of prompts lets each do its own
thing and keeps `enrich_segments` free of backend knowledge.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from mashup.jsonreply import JSONValue, format_rules, parse_json_reply

Messages = list[dict[str, Any]]

# Prompts per batched forward pass. Measured on the dev archive with
# Qwen3-4B-Instruct-4bit: 1 prompt at a time runs 2.42s/segment, 4 at a time
# 1.30s/segment, and 8 gives nothing further while using twice the memory.
LOCAL_BATCH = 4
# Enough for five segments of metadata with room for a verbose model.
LOCAL_MAX_TOKENS = 1600

DEFAULT_LOCAL_CHAT_MODEL = "mlx-community/Qwen3-4B-Instruct-2507-4bit"


class ChatError(RuntimeError):
    """A chat backend could not answer."""


@runtime_checkable
class ChatModel(Protocol):
    @property
    def name(self) -> str: ...

    def chat_json(self, messages: Messages, *, schema_hint: str) -> JSONValue: ...

    def chat_json_many(
        self, conversations: Sequence[Messages], *, schema_hint: str, concurrency: int = 4
    ) -> list[JSONValue | None]:
        """Answer several prompts. `None` marks one that failed."""


class LocalChat:
    """An mlx-lm model running in this process.

    mlx and the weights load on first use, so constructing this is free and
    the test suite does not pay for a model it never calls.
    """

    def __init__(
        self,
        model: str = DEFAULT_LOCAL_CHAT_MODEL,
        *,
        max_tokens: int = LOCAL_MAX_TOKENS,
        retries: int = 2,
    ) -> None:
        self.model_id = model
        self.max_tokens = max_tokens
        self.retries = retries
        self._model = None
        self._tokenizer = None

    @property
    def name(self) -> str:
        return f"local:{self.model_id}"

    def _load(self):
        if self._model is not None:
            return self._model, self._tokenizer
        try:
            from mlx_lm import load
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ChatError(
                "local chat needs mlx-lm, which is Apple-silicon only:\n"
                "  uv sync --extra localchat\n"
                "or set MASHUP_CHAT_BACKEND=gateway to use the fleet gateway instead."
            ) from exc
        try:
            self._model, self._tokenizer = load(self.model_id)
        except (OSError, ValueError) as exc:
            raise ChatError(
                f"could not load {self.model_id!r}. It is not in the local "
                f"HuggingFace cache and could not be downloaded: {exc}"
            ) from exc
        return self._model, self._tokenizer

    def _encode(self, messages: Messages, schema_hint: str) -> list[int]:
        _, tokenizer = self._load()
        # Format rules go last so they are the freshest instruction, and in
        # their own message so the caller's prompt stays untouched.
        convo = [*messages, {"role": "system", "content": format_rules(schema_hint)}]
        return tokenizer.apply_chat_template(convo, add_generation_prompt=True)

    def chat_json(self, messages: Messages, *, schema_hint: str) -> JSONValue:
        result = self.chat_json_many([messages], schema_hint=schema_hint)[0]
        if result is None:
            raise ChatError(f"{self.name} never returned valid JSON")
        return result

    def chat_json_many(
        self,
        conversations: Sequence[Messages],
        *,
        schema_hint: str,
        concurrency: int = LOCAL_BATCH,
    ) -> list[JSONValue | None]:
        conversations = list(conversations)
        if not conversations:
            return []

        model, tokenizer = self._load()
        from mlx_lm import batch_generate

        out: list[JSONValue | None] = [None] * len(conversations)
        width = max(1, concurrency)

        for start in range(0, len(conversations), width):
            window = list(range(start, min(start + width, len(conversations))))
            prompts = [self._encode(conversations[i], schema_hint) for i in window]
            response = batch_generate(
                model, tokenizer, prompts=prompts, max_tokens=self.max_tokens, verbose=False
            )
            for i, text in zip(window, response.texts, strict=True):
                try:
                    out[i] = parse_json_reply(text)
                except ValueError:
                    # Leave it None. Enrichment already treats a missing batch
                    # as "retry next run", which costs nothing because
                    # completed segments are persisted.
                    out[i] = None
        return out


def make_chat(cfg, *, gateway=None) -> ChatModel:
    """Build the chat backend named by config."""
    if cfg.chat_backend == "local":
        return LocalChat(cfg.local_chat_model)
    if cfg.chat_backend == "gateway":
        if gateway is None:
            from mashup.gateway import Gateway

            gateway = Gateway(cfg)
        return gateway
    raise ChatError(f"unknown chat backend {cfg.chat_backend!r} (expected 'local' or 'gateway')")
