"""Local HTTP backend for the transcript editor.

The editor is the point where a human overrules the planner, so this server has
one job: hand the browser everything it needs to judge a cut (transcript,
source, timecode, playable media) and take the corrected EDL back.

Design notes:

* **Stdlib only.** `http.server` on localhost is enough for a single-operator
  tool; a web framework would be dependency weight buying nothing.
* **The archive is snapshotted at startup.** SQLite connections are not
  shareable across threads, and one creator's archive is order 10^3 segments —
  a few megabytes of Python objects. Loading once removes the threading problem
  and makes candidate ranking a numpy matmul over memory.
* **Media is served whole, never transcoded.** Range requests are mandatory:
  without 206 responses `<video>` cannot seek, and a preview that cannot seek to
  `render_start` is useless. The client seeks; the server just streams bytes.
* **Only known source paths are served.** The media route takes a `source_id`
  and looks it up in a dict built from the Store — no user-supplied string ever
  reaches the filesystem, so path traversal has nowhere to land.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import re
import socket
import threading
import urllib.parse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import ValidationError

from mashup.config import Config
from mashup.models import EDL, ScoreTerms, Segment, SegmentMeta, Source
from mashup.plan.score import (
    WEIGHT_PROFILES,
    Calibration,
    PlanContext,
    common_entities,
    prepare_context,
    score_sequence,
    term_callback,
    term_duration_fit,
    term_escalation,
    term_source_diversity,
)
from mashup.render.edl_io import load_edl
from mashup.store import Store

# Editor-facing constants.
MAX_BODY_BYTES = 16 * 1024 * 1024
MEDIA_CHUNK = 256 * 1024
EXCERPT_CHARS = 420
DEFAULT_CANDIDATE_LIMIT = 20
MAX_CANDIDATE_LIMIT = 100

# Terms that can be recomputed from the sequence alone, with no embeddings and
# no network. Everything else needs the query vector, which the EDL does not
# carry, so it is only recomputed when the gateway can rebuild it.
OFFLINE_TERMS = ("escalation", "callback", "duration_fit", "source_diversity")

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", ""}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_WORD_RE = re.compile(r"[a-z0-9']+")


# ---- archive snapshot ----------------------------------------------------


@dataclass(frozen=True)
class Archive:
    """Read-only view of the ingested archive, loaded once at startup."""

    segments: list[Segment] = field(default_factory=list)
    by_id: dict[str, Segment] = field(default_factory=dict)
    by_source: dict[str, list[Segment]] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
    # source_id -> resolved absolute media path. The allow-list for /api/media.
    media: dict[str, Path] = field(default_factory=dict)

    @property
    def embedded(self) -> bool:
        return any(s.embedding for s in self.segments)

    def neighbours(self, seg: Segment) -> tuple[Segment | None, Segment | None]:
        siblings = self.by_source.get(seg.source_id, [])
        try:
            i = next(n for n, s in enumerate(siblings) if s.id == seg.id)
        except StopIteration:
            return None, None
        prev = siblings[i - 1] if i > 0 else None
        nxt = siblings[i + 1] if i + 1 < len(siblings) else None
        return prev, nxt


def load_archive(cfg: Config, edl: EDL | None = None) -> Archive:
    """Snapshot sources + segments from the store, plus any EDL-only sources.

    An EDL can reference a source that is no longer in the database (the
    archive was re-ingested, the row was dropped). Preview should still work for
    a clip the operator is looking at, so those paths join the allow-list.
    """
    segments: list[Segment] = []
    sources: dict[str, Source] = {}
    if cfg.db_path.exists():
        with Store(cfg.db_path) as store:
            sources = {s.id: s for s in store.get_sources()}
            segments = store.get_segments()

    media: dict[str, Path] = {}
    for sid, src in sources.items():
        media[sid] = Path(src.path).expanduser().resolve()
    if edl is not None:
        for clip in edl.clips:
            if clip.source_id not in media and clip.source_path:
                media[clip.source_id] = Path(clip.source_path).expanduser().resolve()

    by_source: dict[str, list[Segment]] = {}
    for seg in segments:
        by_source.setdefault(seg.source_id, []).append(seg)
    for group in by_source.values():
        group.sort(key=lambda s: (s.start, s.id))

    return Archive(
        segments=segments,
        by_id={s.id: s for s in segments},
        by_source=by_source,
        sources=sources,
        media=media,
    )


# ---- scoring -------------------------------------------------------------


@dataclass
class ScoreReport:
    terms: dict[str, float]
    score: float
    # "full"    — every term recomputed against the original query vector.
    # "partial" — only the terms that need no embeddings; the rest carried over.
    mode: str
    recomputed: list[str]


def _unit(vec: list[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    return arr / max(float(np.linalg.norm(arr)), 1e-8)


def _sequence(edl: EDL, archive: Archive) -> list[Segment]:
    """Rebuild the planner's segment sequence from the edited clip list.

    Prefers the stored segment (it carries entities, required_context and the
    embedding) and synthesises a stand-in from the clip when the segment has
    left the archive.
    """
    seq: list[Segment] = []
    for clip in edl.clips:
        member_ids = clip.segment_ids or [clip.segment_id]
        members = [archive.by_id.get(segment_id) for segment_id in member_ids]
        if len(members) > 1 and all(member is not None for member in members):
            from mashup.segment.editorial import merge_editorial_bit

            stored_members = [member for member in members if member is not None]
            anchor = archive.by_id.get(clip.segment_id) or stored_members[0]
            merged = merge_editorial_bit(stored_members, anchor)
            seq.append(
                merged.model_copy(
                    update={
                        "start": clip.start,
                        "end": clip.end,
                        "text": clip.text,
                    }
                )
            )
            continue
        stored = archive.by_id.get(clip.segment_id)
        if stored is not None:
            seq.append(stored)
            continue
        seq.append(
            Segment(
                id=clip.segment_id,
                source_id=clip.source_id,
                start=clip.start,
                end=clip.end,
                text=clip.text,
                cue_start=0,
                cue_end=0,
                meta=SegmentMeta(
                    topic=list(clip.topics),
                    role=clip.role,
                    summary=clip.summary,
                    energy=clip.energy,
                ),
            )
        )
    return seq


def _lexical_sim(a: Segment, b: Segment) -> float:
    wa = set(_WORD_RE.findall(a.text.lower()))
    wb = set(_WORD_RE.findall(b.text.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _embedding_sim(a: Segment, b: Segment) -> float:
    if not a.embedding or not b.embedding:
        return _lexical_sim(a, b)
    return float(_unit(a.embedding) @ _unit(b.embedding))


def _weights(edl: EDL) -> dict[str, float]:
    return edl.weights or WEIGHT_PROFILES.get(edl.strategy, {})


def rescore(edl: EDL, archive: Archive, ctx: PlanContext | None) -> ScoreReport:
    """Recompute what can honestly be recomputed for an edited sequence.

    With `ctx` (the planner's query vector, beats and context embeddings) every
    term is recomputed exactly as the planner would. Without it — no gateway, no
    embeddings — only the four structural terms are recomputed and the rest are
    carried forward, so the editor never shows a fabricated relevance number.
    """
    seq = _sequence(edl, archive)
    weights = _weights(edl)

    if ctx is not None:
        ctx.target_duration = edl.target_duration
        terms = score_sequence(seq, ctx, _embedding_sim)
        score = terms.total(weights) if weights else edl.score
        return ScoreReport(
            terms=terms.model_dump(),
            score=score,
            mode="full",
            recomputed=sorted(terms.model_dump()),
        )

    terms = edl.terms.model_copy(
        update={
            "escalation": term_escalation(seq),
            # Derivable from the archive alone, so the degraded path is still
            # protected from reading the host's name as a running gag.
            "callback": term_callback(seq, common_entities(archive.segments)),
            "duration_fit": term_duration_fit(seq, edl.target_duration),
            "source_diversity": term_source_diversity(seq),
        }
    )
    score = terms.total(weights) if weights else edl.score
    return ScoreReport(
        terms=terms.model_dump(),
        score=score,
        mode="partial",
        recomputed=list(OFFLINE_TERMS),
    )


# ---- server state --------------------------------------------------------


def default_web_dist() -> Path:
    """`web/dist` next to the package checkout, unless overridden."""
    override = os.getenv("MASHUP_WEB_DIST")
    if override:
        return Path(override).expanduser().resolve()
    # src/mashup/serve.py -> src/mashup -> src -> repo root
    return Path(__file__).resolve().parents[2] / "web" / "dist"


class EditorState:
    """Everything the handlers share. All mutation goes through `lock`."""

    def __init__(self, edl_path: Path, cfg: Config, *, web_dist: Path | None = None) -> None:
        self.edl_path = Path(edl_path).expanduser().resolve()
        self.cfg = cfg
        self.web_dist = (web_dist or default_web_dist()).resolve()
        self.lock = threading.Lock()
        self.edl = load_edl(self.edl_path)
        self.archive = load_archive(cfg, self.edl)
        self._ctx: PlanContext | None = None
        self._ctx_prompt: str | None = None
        self._ctx_failed = False
        self._query_vecs: dict[str, list[float]] = {}

    # -- model-backed helpers (best effort; never fatal) -------------------

    @property
    def offline(self) -> bool:
        """No embeddings available, so only the structural terms can be scored."""
        if os.getenv("MASHUP_SERVE_OFFLINE"):
            return True
        if not self.archive.embedded:
            return True
        if self.cfg.embed_backend == "local":
            # Nothing to reach for; the encoder runs in this process.
            return False
        # Without a key the only hope is the on-disk gateway cache from `build`.
        return not (self.cfg.gateway_api_key or (self.cfg.cache_dir / "gateway").exists())

    def _gateway(self):
        from mashup.gateway import Gateway

        # One quick attempt: a stalled PUT is worse than a degraded score.
        return Gateway(self.cfg, timeout=20.0, retry_attempts=1)

    def _embedder(self, gw):
        from mashup.embedding import make_embedder

        return make_embedder(self.cfg, gateway=gw)

    def plan_context(self, edl: EDL) -> PlanContext | None:
        """Rebuild the planner's scoring context, or None if we cannot.

        The EDL does not persist the query vector, so this re-derives it. With
        the local backend that is a few milliseconds of CPU; with the gateway,
        `build` already made the identical calls, so the content-addressed
        cache normally answers without touching the network.
        """
        if self.offline or self._ctx_failed:
            return None
        if self._ctx is not None and self._ctx_prompt == edl.prompt:
            return self._ctx
        try:
            from mashup.plan.prompt import parse_request

            gw = self._gateway()
            try:
                embedder = self._embedder(gw)

                def embed_query(texts: list[str]) -> list[list[float]]:
                    return embedder.embed(texts, kind="query")

                request = parse_request(
                    edl.prompt,
                    gw if self.cfg.gateway_api_key else None,
                )
                ctx = PlanContext(
                    query_vec=embed_query([request.query])[0],
                    target_duration=edl.target_duration,
                    source_ordinals={sid: s.ordinal for sid, s in self.archive.sources.items()},
                    beat_vecs=embed_query(request.beats) if request.beats else [],
                    beat_labels=list(request.beats),
                )
                prepare_context(
                    ctx,
                    self.archive.segments,
                    lambda texts: embedder.embed(texts, kind="document"),
                    # The build measured its thresholds on the candidate pool;
                    # re-measuring here against the whole archive would give a
                    # score that silently means something different.
                    calibrate=not edl.calibration,
                )
                if edl.calibration:
                    ctx.calibration = Calibration.from_dict(edl.calibration)
            finally:
                gw.close()
        except Exception:  # noqa: BLE001 — degraded scoring beats a 500
            self._ctx_failed = True
            return None
        self._ctx, self._ctx_prompt = ctx, edl.prompt
        return ctx

    def query_vector(self, text: str) -> list[float] | None:
        if self.offline or not text.strip():
            return None
        cached = self._query_vecs.get(text)
        if cached is not None:
            return cached
        try:
            gw = self._gateway()
            try:
                vec = self._embedder(gw).embed([text], kind="query")[0]
            finally:
                gw.close()
        except Exception:  # noqa: BLE001 — fall back to substring ranking
            return None
        self._query_vecs[text] = vec
        return vec

    # -- document ---------------------------------------------------------

    def read_edl(self) -> EDL:
        with self.lock:
            # Keep serving the last good document rather than 500ing on a
            # half-written file.
            with contextlib.suppress(OSError, ValidationError, ValueError):
                self.edl = load_edl(self.edl_path)
            return self.edl

    def write_edl(self, edl: EDL) -> tuple[EDL, ScoreReport]:
        """Renumber, rescore, write atomically, return the saved document."""
        for i, clip in enumerate(edl.clips):
            clip.index = i
        report = rescore(edl, self.archive, self.plan_context(edl))
        edl.terms = ScoreTerms.model_validate(report.terms)
        edl.score = report.score

        payload = json.dumps(edl.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        with self.lock:
            self.edl_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.edl_path.with_name(self.edl_path.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.edl_path)
            self.edl = edl
        return edl, report

    # -- media allow-list --------------------------------------------------

    def media_path(self, source_id: str) -> Path | None:
        return self.archive.media.get(source_id)


# ---- serialisation helpers ----------------------------------------------


def _excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rsplit(" ", 1)[0] + "…"


def _segment_brief(
    seg: Segment, archive: Archive, *, relevance: float | None = None, full: bool = False
) -> dict:
    src = archive.sources.get(seg.source_id)
    out: dict[str, Any] = {
        "id": seg.id,
        "source_id": seg.source_id,
        "source_title": src.title if src else seg.source_id,
        # The editor needs this to build a replacement Clip (Clip.source_path
        # is required by the model contract).
        "source_path": src.path if src else "",
        "start": seg.start,
        "end": seg.end,
        "duration": seg.duration,
        "summary": seg.meta.summary,
        "text": seg.text if full else _excerpt(seg.text),
        "role": seg.meta.role.value,
        "energy": seg.meta.energy,
        "topics": list(seg.meta.topic),
    }
    if relevance is not None:
        out["relevance"] = relevance
    return out


def _segment_detail(seg: Segment, archive: Archive) -> dict:
    prev, nxt = archive.neighbours(seg)
    src = archive.sources.get(seg.source_id)
    detail = _segment_brief(seg, archive)
    detail.update(
        {
            "text": seg.text,
            "cue_start": seg.cue_start,
            "cue_end": seg.cue_end,
            "entities": list(seg.meta.entities),
            "required_context": list(seg.meta.required_context),
            "can_open": seg.meta.can_open,
            "can_end": seg.meta.can_end,
            "has_embedding": bool(seg.embedding),
        }
    )
    return {
        "segment": detail,
        # Full text, not an excerpt: "extend" merges a neighbour's transcript
        # into the clip, and a truncated merge would silently lose material.
        "prev": _segment_brief(prev, archive, full=True) if prev else None,
        "next": _segment_brief(nxt, archive, full=True) if nxt else None,
        "source": {
            "id": seg.source_id,
            "title": src.title if src else seg.source_id,
            "duration": src.duration if src else 0.0,
            "has_video": bool(src.has_video) if src else True,
        },
    }


# ---- candidate ranking ---------------------------------------------------


def _substring_rank(segments: list[Segment], query: str) -> list[tuple[Segment, float]]:
    """Token-overlap fallback for archives that were never embedded."""
    tokens = set(_WORD_RE.findall(query.lower()))
    if not tokens:
        return []
    scored: list[tuple[Segment, float]] = []
    for seg in segments:
        hay = " ".join([seg.meta.summary, seg.text, " ".join(seg.meta.topic)]).lower()
        hits = sum(1 for t in tokens if t in hay)
        if not hits:
            continue
        occurrences = sum(hay.count(t) for t in tokens)
        # Matching more distinct query words dominates; repetition breaks ties.
        scored.append((seg, hits + min(occurrences, 20) / 100.0))
    scored.sort(key=lambda pair: (-pair[1], pair[0].source_id, pair[0].start))
    return scored


def rank_candidates(
    archive: Archive, query: str, limit: int, query_vec: list[float] | None
) -> tuple[str, list[tuple[Segment, float]]]:
    if not query.strip():
        return "browse", [(s, 0.0) for s in archive.segments[:limit]]

    embedded = [s for s in archive.segments if s.embedding]
    if query_vec and embedded:
        mat = np.vstack([_unit(s.embedding or []) for s in embedded])
        sims = mat @ _unit(query_vec)
        order = np.argsort(-sims)[:limit]
        return "embedding", [(embedded[int(i)], float(sims[int(i)])) for i in order]

    return "substring", _substring_rank(archive.segments, query)[:limit]


# ---- HTTP ----------------------------------------------------------------


class _RangeError(Exception):
    """Requested range cannot be satisfied."""


def parse_range(value: str, size: int) -> tuple[int, int] | None:
    """Return an inclusive (start, end) byte span, or None for a full body."""
    if not value:
        return None
    match = _RANGE_RE.match(value.strip())
    if not match:
        # Multi-range and non-byte units: answer with the whole file, which is
        # a legal response to any Range request.
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not first:
        length = int(last)
        if length <= 0:
            raise _RangeError(value)
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
        end = min(end, size - 1)
    if start >= size or start > end:
        raise _RangeError(value)
    return start, end


class EditorHandler(BaseHTTPRequestHandler):
    server_version = "mashup-editor/1"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    @property
    def state(self) -> EditorState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002 - stdlib signature
        if os.getenv("MASHUP_SERVE_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(
        self,
        status: HTTPStatus | int,
        body: bytes = b"",
        *,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
        head_only: bool = False,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body and not head_only:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, content_type="application/json; charset=utf-8", headers=headers)

    def _error(self, status: int, message: str, **extra: Any) -> None:
        self._json({"error": message, **extra}, status=status)

    def _text(self, status: int, message: str) -> None:
        self._send(
            status,
            message.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )

    # -- routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._route(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        self._route(head_only=True)

    def _route(self, *, head_only: bool) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/api/edl":
            self._get_edl()
        elif path == "/api/candidates":
            self._get_candidates(params)
        elif path.startswith("/api/segment/"):
            self._get_segment(path[len("/api/segment/") :])
        elif path.startswith("/api/media/"):
            self._get_media(parsed.path[len("/api/media/") :], params, head_only=head_only)
        elif path.startswith("/api/"):
            self._error(404, f"no such endpoint: {path}")
        else:
            self._serve_static(path, head_only=head_only)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/api/edl":
            self._error(404, f"no such endpoint: {parsed.path}")
            return
        self._put_edl()

    # -- endpoints --------------------------------------------------------

    def _get_edl(self) -> None:
        edl = self.state.read_edl()
        self._json(
            edl.model_dump(mode="json"),
            headers={
                "X-Mashup-Edl-Path": str(self.state.edl_path),
                "X-Mashup-Archive-Embedded": "1" if self.state.archive.embedded else "0",
                "Cache-Control": "no-store",
            },
        )

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(400, "invalid Content-Length")
            return None
        if length <= 0:
            self._error(400, "empty request body")
            return None
        if length > MAX_BODY_BYTES:
            self._error(413, f"body larger than {MAX_BODY_BYTES} bytes")
            return None
        return self.rfile.read(length)

    def _put_edl(self) -> None:
        raw = self._read_body()
        if raw is None:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error(400, f"body is not valid JSON: {exc}")
            return
        try:
            edl = EDL.model_validate(payload)
        except ValidationError as exc:
            self._error(422, "EDL failed validation", detail=exc.errors(include_url=False))
            return

        try:
            saved, report = self.state.write_edl(edl)
        except OSError as exc:
            self._error(500, f"could not write {self.state.edl_path}: {exc}")
            return

        self._json(
            saved.model_dump(mode="json"),
            headers={
                "X-Mashup-Score-Mode": report.mode,
                "X-Mashup-Score-Recomputed": ",".join(report.recomputed),
                "Cache-Control": "no-store",
            },
        )

    def _get_candidates(self, params: dict[str, list[str]]) -> None:
        query = (params.get("q") or [""])[0]
        try:
            limit = int((params.get("limit") or [DEFAULT_CANDIDATE_LIMIT])[0])
        except ValueError:
            limit = DEFAULT_CANDIDATE_LIMIT
        limit = max(1, min(limit, MAX_CANDIDATE_LIMIT))

        archive = self.state.archive
        vec = self.state.query_vector(query) if query.strip() else None
        mode, ranked = rank_candidates(archive, query, limit, vec)
        self._json(
            {
                "query": query,
                "mode": mode,
                "count": len(ranked),
                "results": [
                    _segment_brief(seg, archive, relevance=score if mode != "browse" else None)
                    for seg, score in ranked
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    def _get_segment(self, segment_id: str) -> None:
        seg = self.state.archive.by_id.get(segment_id)
        if seg is None:
            self._error(404, f"unknown segment: {segment_id}")
            return
        self._json(_segment_detail(seg, self.state.archive))

    def _get_media(self, raw_id: str, params: dict[str, list[str]], *, head_only: bool) -> None:
        token = urllib.parse.unquote(raw_id)
        # No user string is ever joined onto a path — the id must key the
        # allow-list exactly — but reject obvious traversal loudly anyway.
        if not token or any(c in token for c in ("/", "\\", "\0")) or ".." in token:
            self._error(403, "media is addressed by source_id, not by path")
            return
        path = self.state.media_path(token)
        if path is None:
            self._error(404, f"unknown source: {token}")
            return
        if path not in set(self.state.archive.media.values()):  # defence in depth
            self._error(403, "source path is not in the archive allow-list")
            return
        if not path.is_file():
            self._error(404, f"media file is missing: {path}")
            return

        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        extra = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            # Echoed so the client can confirm which clip window it asked for.
            "X-Clip-Start": (params.get("start") or ["0"])[0],
            "X-Clip-End": (params.get("end") or [""])[0],
        }

        try:
            span = parse_range(self.headers.get("Range") or "", size)
        except _RangeError:
            self._send(
                416,
                b"",
                content_type=ctype,
                headers={**extra, "Content-Range": f"bytes */{size}"},
            )
            return

        if span is None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            if not head_only:
                self._stream(path, 0, size - 1)
            return

        start, end = span
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        if not head_only:
            self._stream(path, start, end)

    def _stream(self, path: Path, start: int, end: int) -> None:
        remaining = end - start + 1
        try:
            with path.open("rb") as fh:
                fh.seek(start)
                while remaining > 0:
                    chunk = fh.read(min(MEDIA_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # Normal: the browser aborts the request on every seek.
            pass

    # -- static site ------------------------------------------------------

    def _serve_static(self, path: str, *, head_only: bool) -> None:
        dist = self.state.web_dist
        index = dist / "index.html"
        if not index.is_file():
            self._text(
                503,
                "The editor UI has not been built.\n\n"
                f"Expected: {index}\n\n"
                "Build it with:\n"
                "  cd web && pnpm install && pnpm build\n\n"
                "Or run the Astro dev server (it proxies /api here):\n"
                "  cd web && pnpm dev\n",
            )
            return

        rel = path.lstrip("/") or "index.html"
        target = (dist / rel).resolve()
        if not target.is_relative_to(dist):
            self._error(403, "path outside the web root")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # Single-page app: unknown routes fall back to the shell, but a
            # missing asset should still 404 so build breakage is visible.
            if Path(rel).suffix:
                self._error(404, f"no such asset: {rel}")
                return
            target = index

        try:
            body = target.read_bytes()
        except OSError as exc:
            self._error(500, f"could not read {target}: {exc}")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype += "; charset=utf-8"
        self._send(
            200,
            body,
            content_type=ctype,
            headers={"Cache-Control": "no-store" if target == index else "max-age=300"},
            head_only=head_only,
        )


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # A browser holding a media connection must never block the API.
    address_family = socket.AF_INET

    def __init__(self, address: tuple[str, int], state: EditorState) -> None:
        self.state = state
        super().__init__(address, EditorHandler)


def build_server(
    edl_path: Path,
    cfg: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    web_dist: Path | None = None,
) -> EditorServer:
    """Construct (but do not run) the editor server. Loopback binds only."""
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(f"editor binds to loopback only, refusing host {host!r}")
    state = EditorState(edl_path, cfg, web_dist=web_dist)
    return EditorServer((host or "127.0.0.1", port), state)


def serve(edl_path: Path, cfg: Config, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the editor for `edl_path` until interrupted."""
    server = build_server(edl_path, cfg, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    state: EditorState = server.state
    counts = f"{len(state.archive.segments)} segments, {len(state.archive.sources)} sources"
    lines = [
        f"mashup editor  http://{bound_host}:{bound_port}",
        f"  edl      {state.edl_path}",
        f"  archive  {counts}",
    ]
    if not (state.web_dist / "index.html").is_file():
        lines.append(f"  ui       not built ({state.web_dist}) — run `pnpm build` in web/")
    print("\n".join(lines), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
