"""Persistence, with an emphasis on vector identity.

Storing which model produced a vector is not bookkeeping. Two encoders of the
same width produce vectors that mix without any dimension check noticing, and
the result is a retrieval layer that ranks confidently and meaninglessly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import make_segment

from mashup.models import Cue, Segment, Source
from mashup.store import Store


def make_source(source_id: str = "ep01") -> Source:
    return Source(
        id=source_id,
        path=f"/archive/{source_id}.mp4",
        title=source_id,
        duration=600.0,
        has_video=True,
        ordinal=1,
    )


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "mashup.db") as st:
        st.upsert_source(make_source(), [Cue(index=0, start=0.0, end=1.0, text="hello")])
        # Ingest produces segments with no vectors; embedding is a later stage.
        fresh = [make_segment("ep01-0001"), make_segment("ep01-0002")]
        for seg in fresh:
            seg.embedding = None
        st.replace_segments("ep01", fresh)
        yield st


def embedded(segments: list[Segment], value: float) -> list[Segment]:
    for seg in segments:
        seg.embedding = [value, 1.0 - value]
    return segments


def test_vectors_round_trip_with_their_model(store: Store) -> None:
    segments = embedded(store.get_segments(), 0.5)
    store.update_segment_embeddings(segments, "local:BAAI/bge-base-en-v1.5")

    assert store.embedding_models() == {"local:BAAI/bge-base-en-v1.5": 2}
    assert store.get_segments()[0].embedding == pytest.approx([0.5, 0.5])


def test_two_models_are_both_reported(store: Store) -> None:
    """The condition that makes every similarity in the pipeline a lie."""
    segments = store.get_segments()
    store.update_segment_embeddings(embedded(segments[:1], 0.5), "local:a")
    store.update_segment_embeddings(embedded(segments[1:], 0.5), "gateway:b")

    assert store.embedding_models() == {"local:a": 1, "gateway:b": 1}


def test_clearing_drops_the_model_too(store: Store) -> None:
    store.update_segment_embeddings(embedded(store.get_segments(), 0.5), "local:a")
    store.clear_embeddings()

    assert store.embedding_models() == {}
    assert store.counts()["embedded"] == 0
    assert all(s.embedding is None for s in store.get_segments())


def test_unembedded_segments_are_not_counted(store: Store) -> None:
    segments = store.get_segments()
    store.update_segment_embeddings(embedded(segments[:1], 0.5), "local:a")
    assert store.embedding_models() == {"local:a": 1}


def test_a_database_from_before_the_model_column_is_migrated(tmp_path: Path) -> None:
    """Existing archives must open, not crash, and must declare their vectors
    unattributed rather than pretend they came from the current model."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sources (
            id TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL,
            duration REAL NOT NULL, has_video INTEGER NOT NULL, subtitle_path TEXT,
            subtitle_origin TEXT NOT NULL DEFAULT 'provided', recorded_at TEXT,
            ordinal INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE segments (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL, start REAL NOT NULL,
            end REAL NOT NULL, text TEXT NOT NULL, cue_start INTEGER NOT NULL,
            cue_end INTEGER NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
            embedding BLOB, embedding_dim INTEGER);
        INSERT INTO segments VALUES
            ('ep01-0001','ep01',0,60,'text',0,1,'{}', X'0000803F', 1);
        """
    )
    conn.commit()
    conn.close()

    with Store(db) as store:
        assert store.embedding_models() == {"": 1}
        assert store.counts()["embedded"] == 1


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "twice.db"
    with Store(db) as store:
        store.upsert_source(make_source(), [])
    with Store(db) as store:
        assert store.counts()["sources"] == 1
