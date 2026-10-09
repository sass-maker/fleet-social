"""Cue-level short-form selection and explicit archival visual manifests."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mashup.chat import ChatModel
from mashup.models import EDL, Cue, Segment, ShortReview, VisualInsert
from mashup.retrieve import Candidate

MIN_SHORT_DURATION = 30.0
MAX_SHORT_DURATION = 60.0
WINDOWS_PER_ANCHOR = 12
CONTEXT_SECONDS = 35.0
SHORT_REVIEW_POOL = 120
SHORT_REVIEW_BATCH = 5
SHORT_REVIEW_VERSION = 3
MIN_HOOK_STRENGTH = 0.4
MIN_PAYOFF_STRENGTH = 0.6

_CONTINUATION_START = re.compile(
    r"^(?:[-–—]\s*)?(?:"
    r"yeah|yes|yep|no|exactly|right|so|and|but|because|well|okay|ok|sure|"
    r"also|then|it|this|that|these|those|he|she|they"
    r")\b",
    re.IGNORECASE,
)
_TRAILING_CONNECTOR = re.compile(
    r"\b(?:and|but|so|because|which|to|of|for|with|like|the|a|an)\s*[,.!?-]*$",
    re.IGNORECASE,
)
_DIALOGUE_PREFIX = re.compile(r"^(?:(?:>>|>)[ ]*)+")
_BARE_ACKNOWLEDGMENT = re.compile(
    r"^(?:(?:yeah|yep|yes|exactly|right|okay|ok|sure|totally)[,.!? ]*)+$",
    re.IGNORECASE,
)
_BOUNDARY_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.IGNORECASE)

SHORT_REVIEW_SYSTEM_PROMPT = """You are a strict short-form podcast editor. Judge \
the exact source window, not whether the general topic is interesting.

Set can_open true only if the exact first words work cold. Set it false for an \
answer, continuation, unresolved reference, or opening that assumes omitted setup.

Set can_end true only if the exact final words both complete the thought and land \
the clip. A grammatical sentence is not enough. Set it false for trailing setup, \
an explanation that continues, a handoff question, an acknowledgment, or a cut \
that makes the viewer expect the next sentence.

hook_strength is 0 to 1: 0 is confusing or inert; 0.5 is clear; 0.8 is immediately \
specific or provocative. payoff_strength is 0 to 1: 0 is no landing; 0.5 is a \
basic conclusion; 0.8 is a memorable takeaway, contrast, decision, reveal, or \
punch. Use CONTEXT BEFORE and CONTEXT AFTER only to detect omitted setup or a \
continued ending. Base the judgment on the literal first and final words of EXACT \
WINDOW; do not paraphrase them into a stronger hook or payoff. Copy 3 to 8 literal \
words from each boundary into opening_quote and ending_quote. Echo every id in the \
same order."""

SHORT_REVIEW_SCHEMA_HINT = """[
  {"id": "<the id given>", "can_open": false, "can_end": false,
   "opening_quote": "first 3 to 8 literal words",
   "ending_quote": "final 3 to 8 literal words",
   "hook_strength": 0.0, "payoff_strength": 0.0,
   "reason": "one short boundary and payoff diagnosis"}
]"""


class ShortPlanningError(ValueError):
    """The archive cannot produce a complete short under the duration contract."""


def validate_short_duration(target: float) -> float:
    if not MIN_SHORT_DURATION <= target <= MAX_SHORT_DURATION:
        raise ValueError(
            f"short duration must be between {MIN_SHORT_DURATION:.0f} and "
            f"{MAX_SHORT_DURATION:.0f} seconds"
        )
    return target


def _starts_cleanly(text: str) -> bool:
    stripped = text.strip()
    spoken = _DIALOGUE_PREFIX.sub("", stripped).lstrip("-–— ").strip()
    first_alpha = next((char for char in spoken if char.isalpha()), "")
    return bool(first_alpha and first_alpha.isupper() and not _CONTINUATION_START.match(spoken))


def _ends_cleanly(text: str) -> bool:
    stripped = text.strip()
    spoken = _DIALOGUE_PREFIX.sub("", stripped).strip()
    return bool(
        spoken
        and spoken[-1] in ".?!"
        and not _TRAILING_CONNECTOR.search(spoken)
        and not _BARE_ACKNOWLEDGMENT.fullmatch(spoken)
    )


def cue_window_candidates(
    anchor: Candidate,
    cues: Sequence[Cue],
    *,
    target: float,
    windows_per_anchor: int = WINDOWS_PER_ANCHOR,
) -> list[Candidate]:
    """Build complete target-sized cue windows containing an anchor's midpoint."""
    validate_short_duration(target)
    segment = anchor.segment
    midpoint = (segment.start + segment.end) / 2
    max_duration = min(MAX_SHORT_DURATION, target * 1.15)
    nearby = [
        cue
        for cue in cues
        if cue.end >= segment.start - CONTEXT_SECONDS
        and cue.start <= segment.end + CONTEXT_SECONDS
        and cue.text.strip()
    ]
    starts = [cue for cue in nearby if cue.start <= midpoint and _starts_cleanly(cue.text)]
    ends = [cue for cue in nearby if cue.end >= midpoint and _ends_cleanly(cue.text)]

    ranked: list[tuple[tuple[float, int, float], Candidate]] = []
    for first in starts:
        for last in ends:
            if last.index < first.index:
                continue
            duration = last.end - first.start
            if not MIN_SHORT_DURATION <= duration <= max_duration:
                continue
            members = [cue for cue in nearby if first.index <= cue.index <= last.index]
            if not members or members[0].index != first.index or members[-1].index != last.index:
                continue
            text = " ".join(cue.text.strip() for cue in members)
            window = segment.model_copy(
                update={
                    "id": f"short:{segment.source_id}:{first.index}-{last.index}",
                    "start": first.start,
                    "end": last.end,
                    "text": text,
                    "cue_start": first.index,
                    "cue_end": last.index,
                    # Exact-window editorial review owns these claims. Do not
                    # turn capitalization plus punctuation into fake evidence.
                    "meta": segment.meta.model_copy(update={"can_open": False, "can_end": False}),
                    "member_segment_ids": [segment.id],
                    "anchor_segment_id": segment.id,
                }
            )
            # Prefer the requested length, then question-led openings, then a
            # little more material when two candidates are otherwise equal.
            opening = " ".join(cue.text for cue in members[:8])
            rank = (abs(duration - target), 0 if "?" in opening else 1, -duration)
            ranked.append((rank, Candidate(segment=window, relevance=anchor.relevance)))

    ranked.sort(key=lambda item: item[0])
    return [candidate for _, candidate in ranked[:windows_per_anchor]]


def build_short_candidates(
    anchors: Sequence[Candidate],
    cues_by_source: dict[str, Sequence[Cue]],
    *,
    target: float,
) -> list[Candidate]:
    """Expand retrieved anchors into unique, duration-safe cue windows."""
    validate_short_duration(target)
    unique: dict[tuple[str, int, int], Candidate] = {}
    for anchor in anchors:
        for candidate in cue_window_candidates(
            anchor,
            cues_by_source.get(anchor.segment.source_id, ()),
            target=target,
        ):
            segment = candidate.segment
            key = (segment.source_id, segment.cue_start, segment.cue_end)
            existing = unique.get(key)
            if existing is None or candidate.relevance > existing.relevance:
                unique[key] = candidate
    if not unique:
        raise ShortPlanningError("no retrieved anchor formed a complete 30-60 second cue window")
    return sorted(unique.values(), key=lambda candidate: candidate.relevance, reverse=True)


@dataclass(frozen=True)
class ReviewedShort:
    candidate: Candidate
    review: ShortReview
    rank: float


def _review_pool(candidates: Sequence[Candidate], limit: int) -> list[Candidate]:
    """Interleave source-ranked windows so one episode cannot consume review."""
    by_source: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_source[candidate.segment.source_id].append(candidate)
    groups = [
        sorted(group, key=lambda item: item.relevance, reverse=True) for group in by_source.values()
    ]
    selected: list[Candidate] = []
    position = 0
    while len(selected) < limit:
        added = False
        for group in groups:
            if position < len(group):
                selected.append(group[position])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        position += 1
    return selected


def _short_context(candidate: Candidate, cues: Sequence[Cue]) -> tuple[str, str]:
    segment = candidate.segment
    positions = {cue.index: position for position, cue in enumerate(cues)}
    first = positions.get(segment.cue_start, 0)
    last = positions.get(segment.cue_end, len(cues) - 1)
    before = " ".join(cue.text.strip() for cue in cues[max(0, first - 8) : first])[-1200:]
    after = " ".join(cue.text.strip() for cue in cues[last + 1 : last + 9])[:1200]
    return before.strip(), after.strip()


def _short_review_messages(
    batch: Sequence[tuple[Candidate, tuple[str, str]]],
) -> list[dict[str, str]]:
    blocks = []
    for index, (candidate, (before, after)) in enumerate(batch, start=1):
        segment = candidate.segment
        blocks.append(
            f"### ITEM {index}\n"
            f"id: {segment.id}\n"
            f"CONTEXT BEFORE (not included): {before or '(start of recording)'}\n"
            f"EXACT WINDOW: {segment.text.strip()}\n"
            f"CONTEXT AFTER (not included): {after or '(end of recording)'}"
        )
    return [
        {"role": "system", "content": SHORT_REVIEW_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Judge these {len(batch)} exact short windows. Return a JSON array "
                f"of {len(batch)} objects in order.\n\n" + "\n\n".join(blocks)
            ),
        },
    ]


def _review_items(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, list):
                return value
        return [raw]
    return []


def _short_review_from_item(item: Any) -> ShortReview | None:
    if not isinstance(item, dict):
        return None
    data = {key: item.get(key) for key in ShortReview.model_fields if key in item}
    try:
        return ShortReview.model_validate(data)
    except ValidationError:
        return None


def _quote_matches_boundary(text: str, quote: str, *, opening: bool) -> bool:
    words = _BOUNDARY_WORD.findall(text.lower())
    quoted = _BOUNDARY_WORD.findall(quote.lower())
    if len(quoted) < 3 or len(quoted) > len(words):
        return False
    boundary = words[: len(quoted)] if opening else words[-len(quoted) :]
    return boundary == quoted


def _review_quotes_match(segment: Segment, review: ShortReview) -> bool:
    return _quote_matches_boundary(segment.text, review.opening_quote, opening=True) and (
        _quote_matches_boundary(segment.text, review.ending_quote, opening=False)
    )


def _short_review_key(
    candidate: Candidate,
    context: tuple[str, str],
    model_name: str,
) -> str:
    payload = [
        SHORT_REVIEW_VERSION,
        model_name,
        candidate.segment.id,
        context[0],
        candidate.segment.text,
        context[1],
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _read_short_review(path: Path) -> ShortReview | None:
    try:
        return ShortReview.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _write_short_review(path: Path, review: ShortReview) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(review.model_dump_json(), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def review_short_candidates(
    candidates: Sequence[Candidate],
    cues_by_source: dict[str, Sequence[Cue]],
    chat: ChatModel,
    cache_dir: Path,
    *,
    pool: int = SHORT_REVIEW_POOL,
    concurrency: int = 4,
    min_hook: float = MIN_HOOK_STRENGTH,
    min_payoff: float = MIN_PAYOFF_STRENGTH,
) -> list[ReviewedShort]:
    """Review exact windows and retain only cold-open, landed short clips."""
    selected = _review_pool(candidates, max(1, pool))
    contexts = {
        candidate.segment.id: _short_context(
            candidate, cues_by_source.get(candidate.segment.source_id, ())
        )
        for candidate in selected
    }
    cache_root = Path(cache_dir) / "short-reviews"
    reviews: dict[str, ShortReview] = {}
    todo: list[tuple[Candidate, tuple[str, str], Path]] = []
    for candidate in selected:
        context = contexts[candidate.segment.id]
        path = cache_root / f"{_short_review_key(candidate, context, chat.name)}.json"
        cached = _read_short_review(path)
        if cached is not None:
            reviews[candidate.segment.id] = cached
        else:
            todo.append((candidate, context, path))

    batches = [todo[i : i + SHORT_REVIEW_BATCH] for i in range(0, len(todo), SHORT_REVIEW_BATCH)]
    width = max(1, concurrency)
    for start in range(0, len(batches), width):
        window = batches[start : start + width]
        replies = chat.chat_json_many(
            [
                _short_review_messages([(candidate, context) for candidate, context, _ in batch])
                for batch in window
            ],
            schema_hint=SHORT_REVIEW_SCHEMA_HINT,
            concurrency=width,
        )
        for batch, raw in zip(window, replies, strict=True):
            items = _review_items(raw)
            by_id = {
                str(item.get("id")): item
                for item in items
                if isinstance(item, dict) and item.get("id") is not None
            }
            for position, (candidate, _, path) in enumerate(batch):
                item = by_id.get(candidate.segment.id)
                if item is None and position < len(items):
                    item = items[position]
                review = _short_review_from_item(item)
                if review is None:
                    continue
                reviews[candidate.segment.id] = review
                _write_short_review(path, review)

    qualified: list[ReviewedShort] = []
    for candidate in selected:
        review = reviews.get(candidate.segment.id)
        if (
            review is None
            or not review.can_open
            or not review.can_end
            or not _review_quotes_match(candidate.segment, review)
            or review.hook_strength < min_hook
            or review.payoff_strength < min_payoff
        ):
            continue
        segment = candidate.segment
        reviewed_segment = segment.model_copy(
            update={
                "meta": segment.meta.model_copy(
                    update={"required_context": [], "can_open": True, "can_end": True}
                )
            }
        )
        reviewed_candidate = Candidate(segment=reviewed_segment, relevance=candidate.relevance)
        duration_fit = 1.0 - abs(segment.duration - 45.0) / 45.0
        rank = (
            0.35 * candidate.relevance
            + 0.20 * review.hook_strength
            + 0.35 * review.payoff_strength
            + 0.10 * max(0.0, duration_fit)
        )
        qualified.append(ReviewedShort(reviewed_candidate, review, rank))
    return sorted(qualified, key=lambda item: item.rank, reverse=True)


def select_distinct_segments(segments: Sequence[Segment], *, count: int) -> list[Segment]:
    """Greedily keep ranked short windows without replaying source material."""
    if not 3 <= count <= 5:
        raise ValueError("short batch count must be between 3 and 5")
    selected: list[Segment] = []
    material_ids: set[str] = set()
    for segment in segments:
        if material_ids.intersection(segment.material_ids):
            continue
        if any(
            segment.source_id == other.source_id
            and segment.start < other.end
            and other.start < segment.end
            for other in selected
        ):
            continue
        selected.append(segment)
        material_ids.update(segment.material_ids)
        if len(selected) == count:
            return selected
    raise ShortPlanningError(
        f"archive produced {len(selected)} distinct complete shorts; {count} requested"
    )


def _manifest_rows(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read visual manifest {path}: {exc}") from exc
    if not isinstance(raw, list) or not all(isinstance(row, dict) for row in raw):
        raise ValueError("visual manifest must be a JSON array of objects")
    return raw


def attach_visual_manifest(edl: EDL, path: Path) -> EDL:
    """Validate and persist clip-relative visual decisions in an EDL copy."""
    by_index = {clip.index: clip for clip in edl.clips}
    additions: dict[int, list[VisualInsert]] = {index: [] for index in by_index}
    for position, row in enumerate(_manifest_rows(path), start=1):
        data = dict(row)
        clip_index = data.pop("clip_index", None)
        if not isinstance(clip_index, int) or clip_index not in by_index:
            raise ValueError(f"visual {position} targets unknown clip_index {clip_index!r}")
        visual = VisualInsert.model_validate(data)
        source = Path(visual.source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"visual {position} source does not exist: {source}")
        clip = by_index[clip_index]
        if visual.end > clip.render_duration + 1e-6:
            raise ValueError(
                f"visual {position} ends at {visual.end:.2f}s but clip "
                f"{clip_index} is {clip.render_duration:.2f}s"
            )
        additions[clip_index].append(visual.model_copy(update={"source_path": str(source)}))

    clips = [
        clip.model_copy(update={"visuals": [*clip.visuals, *additions[clip.index]]})
        for clip in edl.clips
    ]
    return edl.model_copy(update={"clips": clips})
