"""Build context-complete planning units from retrieved segment anchors.

This is deliberately a cheap planning-time repair, not a new enrichment
stage. It only combines adjacent segments that have already been transcribed,
enriched, and embedded, so rebuilding a candidate pool repeats no expensive
work and introduces no generated material.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from mashup.chat import ChatModel
from mashup.models import Segment, SegmentMeta
from mashup.retrieve import Candidate

MAX_BIT_MEMBERS = 5
MAX_BIT_DURATION = 300.0
REVIEW_BATCH = 5
REVIEW_PROMPT_VERSION = 1

REVIEW_SYSTEM_PROMPT = """You are a strict podcast clip editor judging exact cut \
boundaries, not the quality or topic of the material.

For each numbered SEGMENT, use CONTEXT BEFORE and CONTEXT AFTER only to detect \
what the segment assumes and whether it trails into the next segment.

Set can_open true only when the exact first words work as the first thing a \
viewer hears cold. Set it false for answers ("yes", "yeah", "exactly"), \
continuations ("so", "and", "but"), unresolved pronouns/references, or any \
opening that makes a viewer ask what was being discussed.

Set can_end true only when the exact final words complete and land the thought. \
Set it false when the sentence, list, explanation, question/answer, or anecdote \
continues into CONTEXT AFTER. Transcript punctuation is unreliable; judge the \
spoken meaning.

required_context lists only facts or premises missing from the SEGMENT itself. \
Be conservative: false positives cost extra source context; false negatives \
make the final edit incoherent. Echo every id in the same order."""

REVIEW_SCHEMA_HINT = """[
  {"id": "<the id given>", "can_open": false, "can_end": false,
   "required_context": ["..."], "reason": "one short boundary diagnosis"}
]"""

_CONTINUATION_START = re.compile(
    r"^(?:[-–—]\s*)?(?:"
    r"yeah|yes|yep|no|exactly|right|so|and|but|because|well|okay|ok|sure|"
    r"also|then|one|two|three|four|five|it|this|that|these|those|he|she|they"
    r")\b",
    re.IGNORECASE,
)
_TRAILING_CONNECTOR = re.compile(
    r"\b(?:and|but|so|because|which|that|to|of|for|with|like|the|a|an)\s*[,.!?-]*$",
    re.IGNORECASE,
)


class EditorialIntegrityError(ValueError):
    """The retrieved pool contains no context-complete planning unit."""


class BoundaryReview(BaseModel):
    can_open: bool = False
    can_end: bool = False
    required_context: list[str] = Field(default_factory=list)
    reason: str = ""


def _normalise_review(review: BoundaryReview) -> BoundaryReview:
    # `can_open` is the direct edit judgement and, by prompt contract, means
    # the viewer needs no prior setup. Small models sometimes still populate
    # required_context with useful background; treating that contradiction as
    # a veto reduced a 220-boundary pilot to zero candidates.
    if review.can_open and review.required_context:
        return review.model_copy(update={"required_context": []})
    return review


def _starts_cleanly(text: str) -> bool:
    stripped = text.strip()
    first_alpha = next((char for char in stripped if char.isalpha()), "")
    return bool(first_alpha and first_alpha.isupper() and not _CONTINUATION_START.match(stripped))


def _ends_cleanly(text: str) -> bool:
    stripped = text.strip()
    return bool(
        stripped and stripped[-1] not in ",:;-" and not _TRAILING_CONNECTOR.search(stripped)
    )


def _as_items(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, list):
                return value
        return [raw]
    return []


def _review_from_item(item: Any) -> BoundaryReview | None:
    if not isinstance(item, dict):
        return None
    data = {key: item.get(key) for key in BoundaryReview.model_fields if key in item}
    context = data.get("required_context")
    if isinstance(context, str):
        data["required_context"] = [context] if context.strip() else []
    try:
        return _normalise_review(BoundaryReview.model_validate(data))
    except ValidationError:
        return None


def _review_messages(
    batch: Sequence[Segment],
    context: Mapping[str, tuple[str, str]],
) -> list[dict[str, str]]:
    blocks = []
    for i, segment in enumerate(batch, start=1):
        before, after = context[segment.id]
        blocks.append(
            f"### ITEM {i}\n"
            f"id: {segment.id}\n"
            f"CONTEXT BEFORE (not part of segment): {before or '(start of recording)'}\n"
            f"SEGMENT: {segment.text.strip()}\n"
            f"CONTEXT AFTER (not part of segment): {after or '(end of recording)'}"
        )
    return [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Judge these {len(batch)} exact clip boundaries. Return a JSON array "
                f"of {len(batch)} objects in order.\n\n" + "\n\n".join(blocks)
            ),
        },
    ]


def _review_context(segments: Sequence[Segment]) -> dict[str, tuple[str, str]]:
    by_source: dict[str, list[Segment]] = defaultdict(list)
    for segment in segments:
        by_source[segment.source_id].append(segment)
    out: dict[str, tuple[str, str]] = {}
    for source_segments in by_source.values():
        source_segments.sort(key=lambda segment: (segment.start, segment.id))
        for i, segment in enumerate(source_segments):
            before = source_segments[i - 1].text[-1000:] if i else ""
            after = source_segments[i + 1].text[:1000] if i + 1 < len(source_segments) else ""
            out[segment.id] = (before.strip(), after.strip())
    return out


def _review_key(
    segment: Segment,
    context: tuple[str, str],
    model_name: str,
) -> str:
    payload = [
        REVIEW_PROMPT_VERSION,
        model_name,
        segment.id,
        context[0],
        segment.text,
        context[1],
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def _read_review(path: Path) -> BoundaryReview | None:
    try:
        return _normalise_review(
            BoundaryReview.model_validate_json(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValidationError):
        return None


def _write_review(path: Path, review: BoundaryReview) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(review.model_dump_json(), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Review output remains usable for this run even if caching fails.
        pass


def review_candidate_boundaries(
    anchors: Sequence[Candidate],
    archive_segments: Sequence[Segment],
    chat: ChatModel,
    cache_dir: Path,
    *,
    concurrency: int = 4,
    max_members: int = MAX_BIT_MEMBERS,
) -> dict[str, BoundaryReview]:
    """Review only stored boundaries that can participate in an anchor span."""
    by_source: dict[str, list[Segment]] = defaultdict(list)
    for segment in archive_segments:
        by_source[segment.source_id].append(segment)
    positions: dict[str, tuple[list[Segment], int]] = {}
    for source_segments in by_source.values():
        source_segments.sort(key=lambda segment: (segment.start, segment.id))
        positions.update(
            {segment.id: (source_segments, i) for i, segment in enumerate(source_segments)}
        )

    needed: dict[str, Segment] = {}
    for candidate in anchors:
        located = positions.get(candidate.segment.id)
        if located is None:
            continue
        source_segments, i = located
        first = max(0, i - max_members + 1)
        last = min(len(source_segments), i + max_members)
        needed.update({segment.id: segment for segment in source_segments[first:last]})

    context = _review_context(archive_segments)
    cache_root = Path(cache_dir) / "editorial-reviews"
    reviews: dict[str, BoundaryReview] = {}
    todo: list[tuple[Segment, Path]] = []
    for segment in needed.values():
        path = cache_root / f"{_review_key(segment, context[segment.id], chat.name)}.json"
        cached = _read_review(path)
        if cached is not None:
            reviews[segment.id] = cached
        else:
            todo.append((segment, path))

    batches = [todo[i : i + REVIEW_BATCH] for i in range(0, len(todo), REVIEW_BATCH)]
    width = max(1, concurrency)
    for start in range(0, len(batches), width):
        window = batches[start : start + width]
        replies = chat.chat_json_many(
            [_review_messages([segment for segment, _ in batch], context) for batch in window],
            schema_hint=REVIEW_SCHEMA_HINT,
            concurrency=width,
        )
        for batch, raw in zip(window, replies, strict=True):
            items = _as_items(raw)
            by_id = {
                str(item.get("id")): item
                for item in items
                if isinstance(item, dict) and item.get("id") is not None
            }
            for position, (segment, path) in enumerate(batch):
                item = by_id.get(segment.id)
                if item is None and position < len(items):
                    item = items[position]
                review = _review_from_item(item)
                if review is None:
                    continue
                reviews[segment.id] = review
                _write_review(path, review)
    return reviews


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _weighted_embedding(members: Sequence[Segment]) -> list[float] | None:
    embedded = [member for member in members if member.embedding]
    if len(embedded) != len(members):
        return None
    dimensions = {len(member.embedding or []) for member in embedded}
    if len(dimensions) != 1:
        return None
    total = sum(max(member.duration, 0.001) for member in embedded)
    return [
        sum((member.embedding or [])[i] * max(member.duration, 0.001) for member in embedded)
        / total
        for i in range(dimensions.pop())
    ]


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / max(na * nb, 1e-8)


def merge_editorial_bit(members: Sequence[Segment], anchor: Segment) -> Segment:
    """Merge one contiguous source span while keeping the anchor's role."""
    if not members:
        raise ValueError("an editorial bit needs at least one member")
    if anchor.id not in {member.id for member in members}:
        raise ValueError("the editorial bit must contain its anchor")
    source_ids = {member.source_id for member in members}
    if len(source_ids) != 1:
        raise ValueError("an editorial bit cannot cross sources")

    duration = sum(max(member.duration, 0.001) for member in members)
    energy = sum(member.meta.energy * max(member.duration, 0.001) for member in members) / duration
    summaries = _ordered_unique(member.meta.summary for member in members)
    member_ids = [member.id for member in members]

    return Segment(
        id=f"bit:{member_ids[0]}..{member_ids[-1]}",
        source_id=members[0].source_id,
        start=members[0].start,
        end=members[-1].end,
        text=" ".join(member.text.strip() for member in members if member.text.strip()),
        cue_start=members[0].cue_start,
        cue_end=members[-1].cue_end,
        meta=SegmentMeta(
            topic=_ordered_unique(topic for member in members for topic in member.meta.topic),
            role=anchor.meta.role,
            summary=" | ".join(summaries),
            # A qualifying bit begins at a self-contained opener and includes
            # every segment through its clean ending.
            required_context=[],
            energy=energy,
            can_open=True,
            can_end=True,
            entities=_ordered_unique(
                entity for member in members for entity in member.meta.entities
            ),
        ),
        embedding=_weighted_embedding(members),
        member_segment_ids=member_ids,
        anchor_segment_id=anchor.id,
    )


def _best_span(
    source_segments: Sequence[Segment],
    anchor_index: int,
    *,
    max_members: int,
    max_duration: float,
    reviews: Mapping[str, BoundaryReview] | None,
) -> list[Segment] | None:
    anchor = source_segments[anchor_index]

    def review_for(segment: Segment) -> BoundaryReview:
        if reviews is not None:
            return reviews.get(segment.id, BoundaryReview())
        return BoundaryReview(
            can_open=segment.meta.can_open,
            can_end=segment.meta.can_end,
            required_context=list(segment.meta.required_context),
        )

    first = max(0, anchor_index - max_members + 1)
    last = min(len(source_segments), anchor_index + max_members)
    starts = [
        i
        for i in range(first, anchor_index + 1)
        if review_for(source_segments[i]).can_open
        and not review_for(source_segments[i]).required_context
        and _starts_cleanly(source_segments[i].text)
    ]
    ends = [
        i
        for i in range(anchor_index, last)
        if review_for(source_segments[i]).can_end and _ends_cleanly(source_segments[i].text)
    ]

    options: list[tuple[int, float, int, int]] = []
    for start in starts:
        for end in ends:
            members = end - start + 1
            duration = source_segments[end].end - source_segments[start].start
            if members <= max_members and duration <= max_duration:
                options.append((members, duration, start, end))
    if not options:
        return None
    _, _, start, end = min(options)
    span = list(source_segments[start : end + 1])
    if anchor.id not in {member.id for member in span}:  # defensive
        return None
    return span


def build_editorial_candidates(
    anchors: Sequence[Candidate],
    archive_segments: Sequence[Segment],
    query_vec: list[float],
    *,
    label: str = "candidate",
    max_members: int = MAX_BIT_MEMBERS,
    max_duration: float = MAX_BIT_DURATION,
    reviews: Mapping[str, BoundaryReview] | None = None,
) -> list[Candidate]:
    """Repair anchors into bounded clean-opening-to-clean-ending spans."""
    by_source: dict[str, list[Segment]] = defaultdict(list)
    for segment in archive_segments:
        by_source[segment.source_id].append(segment)
    positions: dict[str, tuple[list[Segment], int]] = {}
    for source_segments in by_source.values():
        source_segments.sort(key=lambda segment: (segment.start, segment.id))
        positions.update(
            {segment.id: (source_segments, i) for i, segment in enumerate(source_segments)}
        )

    repaired: dict[tuple[str, ...], Candidate] = {}
    rejected = 0
    for candidate in anchors:
        located = positions.get(candidate.segment.id)
        if located is None:
            rejected += 1
            continue
        source_segments, anchor_index = located
        members = _best_span(
            source_segments,
            anchor_index,
            max_members=max_members,
            max_duration=max_duration,
            reviews=reviews,
        )
        if members is None:
            rejected += 1
            continue
        bit = merge_editorial_bit(members, candidate.segment)
        relevance = _cosine(bit.embedding, query_vec)
        key = tuple(bit.member_segment_ids)
        existing = repaired.get(key)
        if existing is None or relevance > existing.relevance:
            repaired[key] = Candidate(segment=bit, relevance=relevance)

    if not repaired:
        raise EditorialIntegrityError(
            f"editorial integrity rejected all {len(anchors)} {label} anchors; "
            f"no bit found a clean opening and ending within "
            f"{max_members} segments / {max_duration:.0f}s"
        )
    return sorted(repaired.values(), key=lambda candidate: candidate.relevance, reverse=True)
