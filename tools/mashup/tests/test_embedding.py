"""Embedding backends.

Most of this runs without torch. The tests that need the real encoder are
gated on the import so the suite stays portable, but they are not optional
where torch exists: the pooling, prefixing and batch-ordering logic is
exactly the kind that looks right and silently ranks badly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from mashup.config import Config
from mashup.embedding import (
    DEFAULT_LOCAL_MODEL,
    LOCAL_MODELS,
    EmbeddingError,
    GatewayEmbedder,
    LocalEmbedder,
    make_embedder,
    resolve_local_model,
)


def cfg(**over: Any) -> Config:
    base = {
        "gateway_url": "http://gateway.invalid",
        "gateway_api_key": "",
        "project_id": "test",
        "chat_model": "stub",
        "embed_model": "stub-embed",
        "workdir": Path("/tmp/mashup-test"),
    }
    return Config(**{**base, **over})


# ---- the model registry --------------------------------------------------


def test_default_model_is_registered() -> None:
    assert DEFAULT_LOCAL_MODEL in LOCAL_MODELS


def test_alias_resolves_to_its_spec() -> None:
    spec = resolve_local_model("bge-base")
    assert spec.repo == "BAAI/bge-base-en-v1.5"
    assert spec.pooling == "cls"
    assert spec.query_prefix, "BGE is trained with a query-side prefix"


def test_bare_repo_id_is_accepted_with_safe_defaults() -> None:
    spec = resolve_local_model("some-org/some-encoder")
    assert spec.repo == "some-org/some-encoder"
    # A wrong prefix hurts more than a missing one, so an unknown model gets
    # none, and mean pooling because that is what most encoders expect.
    assert spec.pooling == "mean"
    assert spec.query_prefix == ""


def test_unknown_alias_names_the_alternatives() -> None:
    with pytest.raises(EmbeddingError, match="bge-base"):
        resolve_local_model("bge-enormous")


# ---- backend selection ---------------------------------------------------


def test_make_embedder_defaults_to_local() -> None:
    assert isinstance(make_embedder(cfg()), LocalEmbedder)


def test_make_embedder_honours_the_gateway_backend() -> None:
    embedder = make_embedder(cfg(embed_backend="gateway"), gateway=StubGateway())
    assert isinstance(embedder, GatewayEmbedder)


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(EmbeddingError, match="unknown embedding backend"):
        make_embedder(cfg(embed_backend="magic"))


# ---- identity ------------------------------------------------------------


class StubGateway:
    """Enough of Gateway for the wrapper, counting the calls it receives."""

    def __init__(self, served: str | None = None) -> None:
        self.config = cfg()
        self.embed_model_used = served
        self.batches: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(len(t)), 0.0] for t in texts]


def test_local_name_identifies_the_repo_not_the_alias() -> None:
    # The alias is ours and could be repointed; the repo is what actually
    # determines the vector space, so that is what gets stored.
    assert LocalEmbedder("bge-base").name == "local:BAAI/bge-base-en-v1.5"


def test_gateway_name_prefers_the_model_that_actually_served() -> None:
    assert GatewayEmbedder(StubGateway()).name == "gateway:stub-embed"
    assert GatewayEmbedder(StubGateway("voyage-3")).name == "gateway:voyage-3"


def test_gateway_backend_ignores_kind() -> None:
    """The OpenAI embeddings endpoint has no way to mark a text as a query, so
    asking for one must not mangle the text or raise."""
    gw = StubGateway()
    embedder = GatewayEmbedder(gw)
    embedder.embed(["hello"], kind="query")
    assert gw.batches == [["hello"]]


# ---- caching and ordering (no model needed) ------------------------------


class CountingEmbedder(LocalEmbedder):
    """LocalEmbedder with the transformer replaced by a deterministic stub."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.encoded: list[list[str]] = []

    def _encode(self, texts: list[str]) -> list[list[float]]:
        self.encoded.append(list(texts))
        return [[float(len(t)), float(sum(map(ord, t)) % 97)] for t in texts]


def test_repeated_text_is_served_from_memory() -> None:
    embedder = CountingEmbedder()
    first = embedder.embed(["alpha", "beta"])
    second = embedder.embed(["beta", "alpha"])

    assert second == [first[1], first[0]]
    assert len(embedder.encoded) == 1, "the second call must not reach the model"


def test_query_and_document_are_cached_separately() -> None:
    """Same text, different side of the comparison, different vector — a
    single-keyed cache would return the document vector for a query."""
    embedder = CountingEmbedder(model="bge-base")
    doc = embedder.embed(["money"], kind="document")
    query = embedder.embed(["money"], kind="query")
    assert doc != query
    assert len(embedder.encoded) == 2


def test_batching_preserves_input_order() -> None:
    """Batches are built longest-first to cut padding waste, so the output has
    to be mapped back or every vector lands on the wrong segment."""
    texts = ["x" * n for n in (1, 40, 7, 200, 3, 90)]
    embedder = CountingEmbedder(batch_size=2)
    vectors = embedder.embed(texts)
    assert [v[0] for v in vectors] == [1.0, 40.0, 7.0, 200.0, 3.0, 90.0]


def test_partial_cache_hits_still_land_on_the_right_texts() -> None:
    embedder = CountingEmbedder(batch_size=2)
    embedder.embed(["bb", "dddd"])
    vectors = embedder.embed(["a", "bb", "ccc", "dddd", "eeeee"])
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_cache_is_bounded() -> None:
    embedder = CountingEmbedder(cache_size=4)
    embedder.embed([f"text-{i}" for i in range(10)])
    assert len(embedder._cache) == 4


def test_empty_input_does_not_touch_the_model() -> None:
    embedder = CountingEmbedder()
    assert embedder.embed([]) == []
    assert embedder.encoded == []


# ---- the real encoder ----------------------------------------------------


@pytest.fixture(scope="module")
def encoder() -> LocalEmbedder:
    """The genuine model, skipped when torch or the weights are absent."""
    embedder = LocalEmbedder("bge-base")
    try:
        embedder.embed(["warm up"])
    except EmbeddingError as exc:  # no torch, or not in the local HF cache
        pytest.skip(str(exc))
    return embedder


def test_vectors_are_unit_length_and_the_advertised_width(encoder: LocalEmbedder) -> None:
    vectors = np.asarray(encoder.embed(["one sentence", "another, longer sentence here"]))
    assert vectors.shape == (2, encoder.spec.dim)
    # Every consumer treats a dot product as cosine similarity.
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_the_query_prefix_actually_changes_the_vector(encoder: LocalEmbedder) -> None:
    as_query = np.asarray(encoder.embed(["arguing about money"], kind="query")[0])
    as_doc = np.asarray(encoder.embed(["arguing about money"], kind="document")[0])
    assert float(as_query @ as_doc) < 0.999


def test_relevant_material_outranks_irrelevant_material(encoder: LocalEmbedder) -> None:
    """The one test that would catch pooling taken from the wrong token."""
    docs = [
        "My wife and I argue about the credit card bill every single month.",
        "The secret word is house. Say the secret word and the duck comes down.",
        "We met at a dance hall in Brooklyn back in nineteen twenty eight.",
    ]
    matrix = np.asarray(encoder.embed(docs, kind="document"))
    query = np.asarray(encoder.embed(["couples fighting over money"], kind="query")[0])
    sims = matrix @ query
    assert int(np.argmax(sims)) == 0
    assert sims[0] - sims[1] > 0.1, sims


def test_truncation_keeps_a_long_segment_usable(encoder: LocalEmbedder) -> None:
    """Real segments run past the context window; that must clamp, not raise."""
    long_text = "the quiz show went on and on. " * 400
    vec = np.asarray(encoder.embed([long_text])[0])
    assert vec.shape == (encoder.spec.dim,)
    assert np.isfinite(vec).all()
