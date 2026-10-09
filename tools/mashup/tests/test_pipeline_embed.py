"""The embed stage's handling of a changed embedding model.

This is the stage that already failed once in production, when the gateway
fell back between providers mid-run and left the archive holding vectors from
two spaces. That only surfaced as a crash because the widths differed. Here
the widths deliberately match, so the only thing standing between a model
swap and silently meaningless retrieval is the recorded model name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_segment

from mashup import pipeline
from mashup.config import Config
from mashup.models import Cue, Source
from mashup.store import Store


class FixedEmbedder:
    """Same width as its sibling below, different vectors."""

    def __init__(self, name: str, value: float) -> None:
        self.name = name
        self.value = value
        self.calls = 0

    def embed(self, texts, *, kind: str = "document") -> list[list[float]]:
        self.calls += len(list(texts))
        return [[self.value, 1.0 - self.value] for _ in texts]


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    config = Config(
        gateway_url="http://gateway.invalid",
        gateway_api_key="",
        project_id="test",
        chat_model="stub",
        embed_model="stub",
        workdir=tmp_path / "work",
    )
    config.ensure_dirs()
    with Store(config.db_path) as store:
        store.upsert_source(
            Source(
                id="ep01",
                path="/archive/ep01.mp4",
                title="ep01",
                duration=600.0,
                has_video=True,
            ),
            [Cue(index=0, start=0.0, end=1.0, text="hello")],
        )
        segments = [make_segment(f"ep01-000{i}") for i in range(1, 4)]
        for seg in segments:
            seg.embedding = None
        store.replace_segments("ep01", segments)
    return config


def run_embed(cfg: Config, embedder, monkeypatch, **kw) -> list[str]:
    notices: list[str] = []
    monkeypatch.setattr(pipeline, "make_embedder", lambda *_a, **_k: embedder)
    pipeline.embed(cfg, notice=notices.append, **kw)
    return notices


def test_first_pass_embeds_everything_and_records_the_model(cfg, monkeypatch) -> None:
    embedder = FixedEmbedder("local:model-a", 0.25)
    run_embed(cfg, embedder, monkeypatch)

    with Store(cfg.db_path) as store:
        assert store.counts()["embedded"] == 3
        assert store.embedding_models() == {"local:model-a": 3}
    assert embedder.calls == 3


def test_rerunning_with_the_same_model_is_free(cfg, monkeypatch) -> None:
    run_embed(cfg, FixedEmbedder("local:model-a", 0.25), monkeypatch)

    second = FixedEmbedder("local:model-a", 0.25)
    notices = run_embed(cfg, second, monkeypatch)
    assert second.calls == 0, "resumability is the whole point of persisting vectors"
    assert notices == []


def test_a_changed_model_re_embeds_the_whole_archive(cfg, monkeypatch) -> None:
    """Both models return two-dimensional vectors, so nothing downstream could
    detect the swap. Only the recorded name can."""
    run_embed(cfg, FixedEmbedder("local:model-a", 0.25), monkeypatch)

    second = FixedEmbedder("local:model-b", 0.75)
    notices = run_embed(cfg, second, monkeypatch)

    assert second.calls == 3
    with Store(cfg.db_path) as store:
        assert store.embedding_models() == {"local:model-b": 3}
        assert all(s.embedding == pytest.approx([0.75, 0.25]) for s in store.get_segments())
    assert notices and "model-a" in notices[0] and "model-b" in notices[0]


def test_vectors_with_no_recorded_model_are_treated_as_stale(cfg, monkeypatch) -> None:
    """An archive embedded before the model was tracked cannot be trusted to
    match the current one, so it is re-embedded rather than assumed."""
    with Store(cfg.db_path) as store:
        segments = store.get_segments()
        for seg in segments:
            seg.embedding = [0.1, 0.9]
        store.update_segment_embeddings(segments)  # no model, as the old code did
        assert store.embedding_models() == {"": 3}

    embedder = FixedEmbedder("local:model-a", 0.25)
    run_embed(cfg, embedder, monkeypatch)

    assert embedder.calls == 3
    with Store(cfg.db_path) as store:
        assert store.embedding_models() == {"local:model-a": 3}


def test_reset_re_embeds_even_when_the_model_is_unchanged(cfg, monkeypatch) -> None:
    run_embed(cfg, FixedEmbedder("local:model-a", 0.25), monkeypatch)

    second = FixedEmbedder("local:model-a", 0.25)
    run_embed(cfg, second, monkeypatch, reset=True)
    assert second.calls == 3
