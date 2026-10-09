"""Offline end-to-end integration test for the whole pipeline.

Builds a synthetic three-source archive with ffmpeg, drives
ingest -> split -> enrich -> embed -> retrieve -> plan -> EDL -> render, and
asserts the properties each stage claims. Nothing here touches the network: the
gateway is replaced by a deterministic stub and `httpx` is fused so a stray
call fails loudly rather than silently skipping the interesting part.

The stub's embeddings are hashed bags of words, so texts that share vocabulary
really are closer together — retrieval, MMR and the scoring terms see genuine
structure rather than noise.

`test_enrichment_metadata_is_persisted` and `test_enrich_is_resumable` exist
because `pipeline.enrich` once discarded the enriched copies `enrich_segments`
returns and wrote the unmodified segments back, so no LLM metadata survived the
stage. Nothing downstream noticed, because every scoring term simply saw
constants. Both are regression guards now.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from mashup import pipeline
from mashup.config import Config
from mashup.models import EDL, Segment, Source
from mashup.plan.planner import DURATION_CEILING
from mashup.render import render
from mashup.render.cut import probe
from mashup.segment.editorial import merge_editorial_bit
from mashup.store import Store

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

# ---- synthetic archive shape --------------------------------------------

# Cue/gap geometry is chosen so the deterministic splitter emits exactly three
# segments per source: beats are pause-delimited atoms, and the wider gap every
# third beat is the edge `group_atoms` prefers once a run passes its target.
CUE_SECONDS = 2.4
INTRA_GAP = 0.3
BEAT_GAP = 1.6
SECTION_GAP = 2.2
LINES_PER_BEAT = 4
BEATS_PER_SECTION = 3
SECTIONS = 3
LINES = LINES_PER_BEAT * BEATS_PER_SECTION * SECTIONS
# Comfortably longer than the transcript, so every snapped cut stays in range.
MEDIA_SECONDS = 112.0
VIDEO_SIZE = "160x120"
VIDEO_FPS = 8

TARGET_SECONDS = 105.0
EMBED_DIM = 64

# Openers the splitter recognises; applied uniformly so they never bias a cut.
_OPENERS = ("So", "Now", "Anyway", "Listen", "I remember", "Here's the thing,", "And then")

# Recurring names the stub reports as `entities`. "lasagna night" and
# "uncle dev" are planted in source 1 and again in source 3, which is the
# cross-recording material the callback strategy exists to find.
ENTITY_VOCAB = ("lasagna night", "uncle dev", "the blue suitcase")

TOPIC_VOCAB: dict[str, tuple[str, ...]] = {
    "family": ("mother", "cousin", "grandma", "aunt", "uncle", "family"),
    "dinner": ("kitchen", "casserole", "bread", "recipe", "lasagna", "dinner"),
    "travel": ("airport", "suitcase", "boarding", "departure", "travel", "seat"),
    "wedding": ("wedding", "speech", "best man", "toast", "seating", "champagne"),
}


@dataclass(frozen=True)
class SourceSpec:
    filename: str
    has_video: bool
    tone_hz: int
    subjects: tuple[str, ...]
    predicates: tuple[str, ...]
    entity: str


SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(
        filename="ep01-lasagna-night.mp4",
        has_video=True,
        tone_hz=330,
        subjects=(
            "my mother's kitchen",
            "uncle dev",
            "the family dinner table",
            "my cousin's casserole",
            "the burnt garlic bread",
            "grandma's recipe card",
        ),
        predicates=(
            "turned into a hostage negotiation",
            "smells like a public apology",
            "has never once run on time",
            "is basically an unpaid internship",
            "still owes me an explanation",
            "gets brought up every december",
        ),
        entity="lasagna night",
    ),
    SourceSpec(
        filename="ep02-airport-security.m4a",
        has_video=False,
        tone_hz=220,
        subjects=(
            "airport security",
            "the blue suitcase",
            "the boarding queue",
            "the man in seat fourteen c",
            "the departure board",
            "my travel pillow",
        ),
        predicates=(
            "treats everyone like a suspect",
            "has opinions about my shoes",
            "moves slower than continental drift",
            "keeps beeping for no reason",
            "was invented to humble me",
            "is a full time job now",
        ),
        entity="the blue suitcase",
    ),
    SourceSpec(
        filename="ep03-wedding-speech.mp4",
        has_video=True,
        tone_hz=440,
        subjects=(
            "the wedding speech",
            "uncle dev",
            "the best man",
            "the seating chart",
            "the champagne toast",
            "lasagna night",
        ),
        predicates=(
            "went on for nine minutes",
            "ended in a standing ovation nobody wanted",
            "was written in the car park",
            "is still being discussed",
            "made my aunt cry twice",
            "should have been rehearsed",
        ),
        entity="lasagna night",
    ),
)


# ---- transcript generation ----------------------------------------------


def script_lines(spec: SourceSpec, count: int = LINES) -> list[str]:
    """Deterministic, varied transcript lines for one source."""
    lines: list[str] = []
    for i in range(count):
        opener = _OPENERS[i % len(_OPENERS)]
        subject = spec.subjects[i % len(spec.subjects)]
        predicate = spec.predicates[(i * 3 + 1) % len(spec.predicates)]
        line = f"{opener} {subject} {predicate}"
        if i % 5 == 0:
            line += f", which is the whole point of {spec.entity}"
        lines.append(line + ".")
    return lines


def cue_spans(lines: Sequence[str]) -> list[tuple[float, float, str]]:
    """Lay lines out on a timeline with intra-beat, beat and section pauses."""
    spans: list[tuple[float, float, str]] = []
    clock = 0.0
    for i, line in enumerate(lines):
        if i:
            if i % (LINES_PER_BEAT * BEATS_PER_SECTION) == 0:
                clock += SECTION_GAP
            elif i % LINES_PER_BEAT == 0:
                clock += BEAT_GAP
            else:
                clock += INTRA_GAP
        spans.append((clock, clock + CUE_SECONDS, line))
        clock += CUE_SECONDS
    return spans


def srt_stamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(path: Path, spans: Sequence[tuple[float, float, str]]) -> None:
    blocks = [
        f"{n}\n{srt_stamp(start)} --> {srt_stamp(end)}\n{text}\n"
        for n, (start, end, text) in enumerate(spans, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def _ffmpeg(args: list[str]) -> None:
    subprocess.run([FFMPEG, "-y", "-nostdin", "-loglevel", "error", *args], check=True)


def build_archive(archive_dir: Path) -> None:
    """Three short sources — two with video, one audio-only — plus SRT sidecars."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    for spec in SOURCE_SPECS:
        media = archive_dir / spec.filename
        tone = f"sine=frequency={spec.tone_hz}:duration={MEDIA_SECONDS}"
        if spec.has_video:
            _ffmpeg(
                [
                    "-f", "lavfi",
                    "-i", f"testsrc2=size={VIDEO_SIZE}:rate={VIDEO_FPS}:duration={MEDIA_SECONDS}",
                    "-f", "lavfi",
                    "-i", tone,
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-shortest",
                    str(media),
                ]
            )  # fmt: skip
        else:
            _ffmpeg(["-f", "lavfi", "-i", tone, "-c:a", "aac", str(media)])
        write_srt(media.with_suffix(".srt"), cue_spans(script_lines(spec)))


# ---- gateway stub --------------------------------------------------------


def pseudo_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    """A hashed bag of words, L2-normalised.

    Not random and not constant: two texts that share vocabulary land on the
    same coordinates, so cosine similarity carries real signal.
    """
    vec = [0.0] * dim
    for token in re.findall(r"[a-z0-9']+", text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        vec[int.from_bytes(digest, "big") % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        # Never hand back a zero vector; cosine against it is meaningless.
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


_ID_RE = re.compile(r"^id: (\S+)$", re.MULTILINE)
_SEGMENT_RE = re.compile(r"^SEGMENT: (.*)$", re.MULTILINE)

_ROLE_CYCLE = ("setup", "development", "punchline", "callback", "closer")


def _salt(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=2).digest(), "big")


def segment_metadata(seg_id: str, text: str) -> dict[str, Any]:
    """Deterministic per-segment metadata, derived from its id and its text."""
    source_id, _, tail = seg_id.rpartition(":")
    ordinal = int(tail)
    salt = _salt(source_id)
    lowered = text.lower()

    entities = [e for e in ENTITY_VOCAB if e in lowered]
    topics = [tag for tag, words in TOPIC_VOCAB.items() if any(w in lowered for w in words)]
    topics = topics[:3] or ["assorted"]

    return {
        "id": seg_id,
        "topic": topics,
        "role": _ROLE_CYCLE[(ordinal + salt) % len(_ROLE_CYCLE)],
        "summary": f"the bit covers {', '.join(topics)}",
        # Anything past a source's opening segment leans on what came before.
        "required_context": [f"who {entities[0]} is"] if entities and ordinal else [],
        "energy": round(min(0.95, 0.2 + 0.25 * ordinal + 0.05 * (salt % 3)), 2),
        "can_open": ordinal == 0,
        "can_end": ordinal == 2,
        "entities": entities,
    }


def enrichment_reply(user_content: str) -> list[dict[str, Any]]:
    ids = _ID_RE.findall(user_content)
    texts = _SEGMENT_RE.findall(user_content)
    assert ids and len(ids) == len(texts), "enrichment prompt shape changed"
    return [segment_metadata(i, t) for i, t in zip(ids, texts, strict=True)]


BRIEF_REPLY: dict[str, Any] = {
    "query": "family dinner arguments, airport security queues and wedding speeches",
    "beats": [
        "the family kitchen and lasagna night",
        "airport security and the blue suitcase",
        "the wedding speech and the champagne toast",
    ],
    "tone": "warm",
}


class StubGateway:
    """Offline stand-in for `Gateway`, counted so resumability is observable.

    Counters are class-level because the pipeline builds a fresh gateway per
    stage; what matters is the total work asked of the model, not per-instance.
    """

    embed_calls = 0
    chat_json_calls = 0
    chat_calls = 0

    def __init__(self, config: Config, **_kwargs: Any) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return "stub:gateway"

    @classmethod
    def counters(cls) -> dict[str, int]:
        return {
            "embed": cls.embed_calls,
            "chat_json": cls.chat_json_calls,
            "chat": cls.chat_calls,
        }

    def embed(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
        type(self).embed_calls += 1
        return [pseudo_embedding(t) for t in texts]

    def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> str:
        type(self).chat_calls += 1
        return "stub reply"

    def chat_json(self, messages: list[dict[str, Any]], *, schema_hint: str, **_kwargs: Any) -> Any:
        type(self).chat_json_calls += 1
        # The enrichment schema is an array; the brief parser's is an object.
        if schema_hint.lstrip().startswith("["):
            if '"hook_strength"' in schema_hint:
                ids = _ID_RE.findall(messages[-1]["content"])
                windows = re.findall(
                    r"EXACT WINDOW: (.*?)\nCONTEXT AFTER",
                    messages[-1]["content"],
                    re.DOTALL,
                )
                return [
                    {
                        "id": segment_id,
                        "can_open": True,
                        "can_end": True,
                        "opening_quote": " ".join(window.split()[:4]),
                        "ending_quote": " ".join(window.split()[-4:]),
                        "hook_strength": 0.8,
                        "payoff_strength": 0.8,
                        "reason": "synthetic short opens cold and lands",
                    }
                    for segment_id, window in zip(ids, windows, strict=True)
                ]
            if '"reason"' in schema_hint:
                ids = _ID_RE.findall(messages[-1]["content"])
                return [
                    {
                        "id": segment_id,
                        "can_open": segment_id.endswith(":0001"),
                        "can_end": segment_id.endswith(":0002"),
                        "required_context": [],
                        "reason": "synthetic clean boundary",
                    }
                    for segment_id in ids
                ]
            return enrichment_reply(messages[-1]["content"])
        return dict(BRIEF_REPLY)

    def chat_json_many(
        self, conversations: Any, *, schema_hint: str, concurrency: int = 4, **_kwargs: Any
    ) -> list[Any]:
        return [self.chat_json(m, schema_hint=schema_hint) for m in conversations]


def _forbid_network(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("the pipeline attempted a network call")


# ---- the run under test --------------------------------------------------


@dataclass
class PipelineRun:
    cfg: Config
    archive: Path
    after_ingest: dict[str, int]
    after_enrich: dict[str, int]
    after_embed: dict[str, int]
    after_rerun: dict[str, int]
    calls_after_first_pass: dict[str, int] = field(default_factory=dict)
    calls_after_rerun: dict[str, int] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    edls: list[EDL] = field(default_factory=list)

    def edl(self, strategy: str) -> EDL:
        return next(e for e in self.edls if e.strategy == strategy)

    @property
    def by_source_id(self) -> dict[str, Source]:
        return {s.id: s for s in self.sources}


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("pipeline-e2e")
    archive = root / "archive"
    build_archive(archive)

    cfg = Config(
        gateway_url="http://gateway.invalid",
        gateway_api_key="stub-key",
        project_id="mashup-test",
        chat_model="stub-chat",
        embed_model="stub-embed",
        workdir=root / "work",
        # Route both model stages through StubGateway rather than the default
        # local backends: this suite is about pipeline wiring, and it must
        # stay hermetic and free of a torch or mlx import.
        embed_backend="gateway",
        chat_backend="gateway",
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pipeline, "Gateway", StubGateway)
        # Any real HTTP attempt is a test failure, not a reason to skip.
        mp.setattr(httpx.Client, "send", _forbid_network)
        StubGateway.embed_calls = 0
        StubGateway.chat_json_calls = 0
        StubGateway.chat_calls = 0

        # `allow_transcribe=False` keeps mlx_whisper out of the picture: a
        # missing sidecar raises instead of quietly transcribing.
        after_ingest = pipeline.ingest(archive, cfg, allow_transcribe=False)
        after_enrich = pipeline.enrich(cfg, concurrency=2)
        after_embed = pipeline.embed(cfg)
        calls_after_first_pass = StubGateway.counters()

        # Resumability: the expensive stages must do nothing the second time.
        pipeline.enrich(cfg, concurrency=2)
        after_rerun = pipeline.embed(cfg)
        calls_after_rerun = StubGateway.counters()

        edls = pipeline.make_mashups(
            "Start with lasagna night in the family kitchen, escalate into airport "
            "security, and finish with the wedding speech",
            cfg,
            target=TARGET_SECONDS,
            include_baselines=True,
        )

        with Store(cfg.db_path) as store:
            segments = store.get_segments()
            sources = store.get_sources()

        yield PipelineRun(
            cfg=cfg,
            archive=archive,
            after_ingest=after_ingest,
            after_enrich=after_enrich,
            after_embed=after_embed,
            after_rerun=after_rerun,
            calls_after_first_pass=calls_after_first_pass,
            calls_after_rerun=calls_after_rerun,
            segments=segments,
            sources=sources,
            edls=edls,
        )


# ---- ingest / split ------------------------------------------------------


def test_ingest_reads_every_source_and_splits_it(run: PipelineRun) -> None:
    assert run.after_ingest["sources"] == len(SOURCE_SPECS) > 1
    assert run.after_ingest["cues"] == LINES * len(SOURCE_SPECS)

    per_source: dict[str, int] = {}
    for seg in run.segments:
        per_source[seg.source_id] = per_source.get(seg.source_id, 0) + 1
    assert len(per_source) == len(SOURCE_SPECS), "every source must contribute segments"
    assert all(n >= 3 for n in per_source.values()), per_source
    assert run.after_ingest["segments"] == sum(per_source.values())

    # Ordinals are the planner's chronology proxy and follow filename order.
    assert [s.ordinal for s in run.sources] == [0, 1, 2]
    assert [Path(s.path).name for s in run.sources] == [s.filename for s in SOURCE_SPECS]
    assert [s.has_video for s in run.sources] == [s.has_video for s in SOURCE_SPECS]
    assert all(s.subtitle_origin == "provided" for s in run.sources)


# ---- enrich / embed ------------------------------------------------------


def test_every_segment_is_embedded(run: PipelineRun) -> None:
    total = run.after_embed["segments"]
    assert total == len(run.segments)
    assert total == run.after_embed["embedded"]
    # NOTE: `counts()["enriched"]` counts rows whose meta is not the literal
    # '{}', but `Store._insert_segments` always writes a serialised default
    # SegmentMeta, so this is true from the moment a segment is inserted and
    # can never report enrichment progress. Kept here only to pin the current
    # contract; the substantive check lives below.
    assert total == run.after_embed["enriched"]

    assert all(s.embedding and len(s.embedding) == EMBED_DIM for s in run.segments)


def test_enrichment_metadata_is_persisted(run: PipelineRun) -> None:
    assert all(s.meta.summary for s in run.segments)
    assert len({s.meta.role for s in run.segments}) > 1
    assert len({s.meta.energy for s in run.segments}) > 1
    assert any(s.meta.can_open for s in run.segments)
    assert any(s.meta.can_end for s in run.segments)
    assert any(s.meta.required_context for s in run.segments)

    # The recurring entity is planted in source 1 and paid off in source 3;
    # without it the callback strategy has nothing to match across recordings.
    carriers = {s.source_id for s in run.segments if "lasagna night" in s.meta.entities}
    assert len(carriers) >= 2, "callback strategy needs material planted in two recordings"


def test_embeddings_carry_real_structure(run: PipelineRun) -> None:
    """Same-source segments must be closer than cross-source ones."""

    def cosine(a: Segment, b: Segment) -> float:
        return sum(x * y for x, y in zip(a.embedding or [], b.embedding or [], strict=True))

    by_source: dict[str, list[Segment]] = {}
    for seg in run.segments:
        by_source.setdefault(seg.source_id, []).append(seg)

    within = [
        cosine(a, b)
        for group in by_source.values()
        for i, a in enumerate(group)
        for b in group[i + 1 :]
    ]
    across = [
        cosine(a, b)
        for sid_a, group_a in by_source.items()
        for sid_b, group_b in by_source.items()
        if sid_a < sid_b
        for a in group_a
        for b in group_b
    ]
    assert within and across
    assert min(within) > max(across)


def test_embed_is_resumable(run: PipelineRun) -> None:
    assert run.calls_after_first_pass["embed"] > 0
    assert run.calls_after_rerun["embed"] == run.calls_after_first_pass["embed"]
    assert run.after_rerun == run.after_embed


def test_enrich_is_resumable(run: PipelineRun) -> None:
    assert run.calls_after_first_pass["chat_json"] > 0
    assert run.calls_after_rerun["chat_json"] == run.calls_after_first_pass["chat_json"]


# ---- planning ------------------------------------------------------------


def test_one_edl_per_strategy_plus_both_baselines(run: PipelineRun) -> None:
    assert [e.strategy for e in run.edls] == [*pipeline.AI_STRATEGIES, "semantic", "random"]
    for edl in run.edls:
        assert edl.clips, f"{edl.strategy} produced an empty timeline"
        assert edl.target_duration == TARGET_SECONDS
        assert edl.rationale


def test_no_edl_repeats_a_segment(run: PipelineRun) -> None:
    for edl in run.edls:
        ids = [c.segment_id for c in edl.clips]
        assert len(ids) == len(set(ids)), f"{edl.strategy} reused a segment"


def test_short_batch_produces_three_non_overlapping_source_windows(run: PipelineRun) -> None:
    batch = pipeline.make_short_batch(
        "a self-contained lesson about family decisions and travel",
        run.cfg,
        count=3,
        target=30,
    )

    assert [edl.strategy for edl in batch] == ["short-01", "short-02", "short-03"]
    clips = [edl.clips[0] for edl in batch]
    for position, left in enumerate(clips):
        for right in clips[position + 1 :]:
            assert left.source_id != right.source_id or not (
                left.start < right.end and right.start < left.end
            )


def test_every_edl_respects_the_duration_ceiling(run: PipelineRun) -> None:
    ceiling = TARGET_SECONDS * DURATION_CEILING
    for edl in run.edls:
        planned = sum(c.end - c.start for c in edl.clips)
        assert planned <= ceiling, (
            f"{edl.strategy} planned {planned:.1f}s over a {ceiling:.1f}s cap"
        )
        assert edl.duration <= ceiling, f"{edl.strategy} renders {edl.duration:.1f}s"


def test_clips_carry_the_stored_segment(run: PipelineRun) -> None:
    """The EDL is the repaired store span projected onto a timeline."""
    by_id = {s.id: s for s in run.segments}
    for edl in run.edls:
        for clip in edl.clips:
            members = [by_id[segment_id] for segment_id in clip.segment_ids]
            expected = merge_editorial_bit(members, by_id[clip.segment_id])
            assert (clip.source_id, clip.start, clip.end) == (
                expected.source_id,
                expected.start,
                expected.end,
            )
            assert clip.text == expected.text
            assert clip.source_title
            assert clip.summary == expected.meta.summary
            assert clip.role == expected.meta.role
            assert clip.energy == expected.meta.energy
            assert clip.topics == list(expected.meta.topic)


def test_chronological_clips_run_forward_through_the_archive(run: PipelineRun) -> None:
    ordinals = {s.id: s.ordinal for s in run.sources}
    keys = [(ordinals[c.source_id], c.start) for c in run.edl("chronological").clips]
    assert keys == sorted(keys)


def test_clip_render_windows_are_valid(run: PipelineRun) -> None:
    sources = run.by_source_id
    for edl in run.edls:
        for i, clip in enumerate(edl.clips):
            assert clip.index == i
            source = sources[clip.source_id]
            assert Path(clip.source_path).exists()
            assert Path(clip.source_path) == Path(source.path)
            assert clip.render_start < clip.render_end
            assert clip.render_start >= 0.0
            assert clip.render_end <= source.duration, (
                f"{edl.strategy} clip {i} ends past the source ({source.duration:.2f}s)"
            )
            # Snapping only ever widens a cut, never narrows it.
            assert clip.render_start <= clip.start
            assert clip.render_end >= clip.end


# ---- render --------------------------------------------------------------


def test_renders_a_playable_mp4_with_a_sidecar(run: PipelineRun, tmp_path: Path) -> None:
    edl = run.edl("chronological")
    out = render(
        edl,
        tmp_path / "mashup.mp4",
        workdir=tmp_path / "render-work",
        subtitles="sidecar",
    )

    assert out.exists() and out.stat().st_size > 0
    info = probe(out)
    assert info.has_video and info.has_audio
    assert info.duration > 0
    assert info.duration == pytest.approx(edl.duration, rel=0.05)

    sidecar = out.with_suffix(".srt")
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> ")
