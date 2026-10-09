"""SQLite persistence for an ingested archive.

Vectors live as float32 blobs and similarity is brute-forced in numpy. At the
scale this tool targets — one creator's archive, order 10^3 segments — an
exact scan costs under a millisecond, so pgvector or an ANN index would be
dependency weight buying nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from mashup.models import Cue, Segment, SegmentMeta, Source

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    title TEXT NOT NULL,
    duration REAL NOT NULL,
    has_video INTEGER NOT NULL,
    subtitle_path TEXT,
    subtitle_origin TEXT NOT NULL DEFAULT 'provided',
    recorded_at TEXT,
    ordinal INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cues (
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT,
    PRIMARY KEY (source_id, idx)
);

CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL,
    cue_start INTEGER NOT NULL,
    cue_end INTEGER NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    embedding BLOB,
    embedding_dim INTEGER,
    -- Which embedder produced the vector. Dimension alone cannot tell two
    -- 384-dimension models apart, and mixing them corrupts retrieval without
    -- raising anything.
    embedding_model TEXT
);

CREATE INDEX IF NOT EXISTS idx_segments_source ON segments(source_id);
"""

# Columns added after the first release, applied to existing databases.
MIGRATIONS = (("segments", "embedding_model", "TEXT"),)


def _to_blob(vec: list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _from_blob(blob: bytes | None, dim: int | None) -> list[float] | None:
    if blob is None or not dim:
        return None
    return np.frombuffer(blob, dtype=np.float32).tolist()


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns missing from a database created by an older version."""
        with self.conn:
            for table, column, decl in MIGRATIONS:
                existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.commit()
        self.close()

    # ---- sources -------------------------------------------------------

    def upsert_source(self, source: Source, cues: list[Cue]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO sources
                   (id, path, title, duration, has_video, subtitle_path,
                    subtitle_origin, recorded_at, ordinal)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     path=excluded.path, title=excluded.title,
                     duration=excluded.duration, has_video=excluded.has_video,
                     subtitle_path=excluded.subtitle_path,
                     subtitle_origin=excluded.subtitle_origin,
                     recorded_at=excluded.recorded_at, ordinal=excluded.ordinal""",
                (
                    source.id,
                    source.path,
                    source.title,
                    source.duration,
                    int(source.has_video),
                    source.subtitle_path,
                    source.subtitle_origin,
                    source.recorded_at,
                    source.ordinal,
                ),
            )
            self.conn.execute("DELETE FROM cues WHERE source_id = ?", (source.id,))
            self.conn.executemany(
                "INSERT INTO cues (source_id, idx, start, end, text, speaker) VALUES (?,?,?,?,?,?)",
                [(source.id, c.index, c.start, c.end, c.text, c.speaker) for c in cues],
            )

    def get_sources(self) -> list[Source]:
        rows = self.conn.execute("SELECT * FROM sources ORDER BY ordinal, id").fetchall()
        return [
            Source(
                id=r["id"],
                path=r["path"],
                title=r["title"],
                duration=r["duration"],
                has_video=bool(r["has_video"]),
                subtitle_path=r["subtitle_path"],
                subtitle_origin=r["subtitle_origin"],
                recorded_at=r["recorded_at"],
                ordinal=r["ordinal"],
            )
            for r in rows
        ]

    def get_cues(self, source_id: str) -> list[Cue]:
        rows = self.conn.execute(
            "SELECT * FROM cues WHERE source_id = ? ORDER BY idx", (source_id,)
        ).fetchall()
        return [
            Cue(
                index=r["idx"],
                start=r["start"],
                end=r["end"],
                text=r["text"],
                speaker=r["speaker"],
            )
            for r in rows
        ]

    # ---- segments ------------------------------------------------------

    def replace_segments(self, source_id: str, segments: list[Segment]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM segments WHERE source_id = ?", (source_id,))
            self._insert_segments(segments)

    def _insert_segments(self, segments: list[Segment]) -> None:
        self.conn.executemany(
            """INSERT INTO segments
               (id, source_id, start, end, text, cue_start, cue_end, meta, embedding, embedding_dim)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    s.id,
                    s.source_id,
                    s.start,
                    s.end,
                    s.text,
                    s.cue_start,
                    s.cue_end,
                    s.meta.model_dump_json(),
                    _to_blob(s.embedding) if s.embedding else None,
                    len(s.embedding) if s.embedding else None,
                )
                for s in segments
            ],
        )

    def update_segment_meta(self, segments: list[Segment]) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE segments SET meta = ? WHERE id = ?",
                [(s.meta.model_dump_json(), s.id) for s in segments],
            )

    def update_segment_embeddings(self, segments: list[Segment], model: str = "") -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE segments SET embedding = ?, embedding_dim = ?, embedding_model = ? "
                "WHERE id = ?",
                [
                    (_to_blob(s.embedding), len(s.embedding), model, s.id)
                    for s in segments
                    if s.embedding
                ],
            )

    def clear_embeddings(self) -> None:
        """Drop every vector. Needed when the embedding model changes, because
        vectors from two models are not comparable."""
        with self.conn:
            self.conn.execute(
                "UPDATE segments SET embedding = NULL, embedding_dim = NULL, embedding_model = NULL"
            )

    def embedding_models(self) -> dict[str, int]:
        """Which embedders produced the stored vectors, and how many each.

        More than one entry means retrieval is comparing incomparable spaces.
        Vectors written before the model was recorded come back under "".
        """
        rows = self.conn.execute(
            "SELECT COALESCE(embedding_model, '') AS model, COUNT(*) AS n FROM segments "
            "WHERE embedding IS NOT NULL GROUP BY model"
        ).fetchall()
        return {r["model"]: int(r["n"]) for r in rows}

    def get_segments(self, *, with_embeddings: bool = True) -> list[Segment]:
        rows = self.conn.execute("SELECT * FROM segments ORDER BY source_id, start").fetchall()
        out: list[Segment] = []
        for r in rows:
            out.append(
                Segment(
                    id=r["id"],
                    source_id=r["source_id"],
                    start=r["start"],
                    end=r["end"],
                    text=r["text"],
                    cue_start=r["cue_start"],
                    cue_end=r["cue_end"],
                    meta=SegmentMeta.model_validate(json.loads(r["meta"])),
                    embedding=(
                        _from_blob(r["embedding"], r["embedding_dim"]) if with_embeddings else None
                    ),
                )
            )
        return out

    def counts(self) -> dict[str, int]:
        def one(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0])

        return {
            "sources": one("SELECT COUNT(*) FROM sources"),
            "cues": one("SELECT COUNT(*) FROM cues"),
            "segments": one("SELECT COUNT(*) FROM segments"),
            # A default SegmentMeta still serialises to a full JSON object, so
            # presence of `meta` proves nothing. A non-empty summary is the
            # marker that the enrichment pass actually ran on this row.
            "enriched": one(
                "SELECT COUNT(*) FROM segments "
                "WHERE COALESCE(json_extract(meta, '$.summary'), '') != ''"
            ),
            "embedded": one("SELECT COUNT(*) FROM segments WHERE embedding IS NOT NULL"),
        }
