"""LLM enrichment of segments.

Fills `SegmentMeta` for every segment. Downstream planners lean hardest on
`required_context` / `can_open` / `can_end` (whether a clip can stand alone)
and on `entities` (what the callback stage matches on), so the prompt is
written around those rather than around tidy summaries.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from pydantic import ValidationError

from mashup.chat import ChatModel
from mashup.models import Role, Segment, SegmentMeta

logger = logging.getLogger(__name__)

# Tunable in one place — this is the prompt the whole pipeline's quality rests on.
SYSTEM_PROMPT = """You analyse a single comedian/podcaster's archive so that clips \
from different recordings can be reassembled into new, themed mashups.

You will be given numbered items. Each item has the SEGMENT (the clip being \
judged) and, around it, CONTEXT BEFORE / CONTEXT AFTER — the neighbouring \
transcript from the same recording. The context is background only: never \
describe or summarise it, use it solely to work out what the segment silently \
assumes.

The single most important judgement you make is whether a clip stands on its \
own or needs prior setup. A clip that references a premise, a name, a bit, or \
a punchline established earlier is NOT self-contained, even if it is funny in \
isolation. Getting this wrong makes the final mashup incoherent, so be strict: \
if a viewer dropped in cold would think "wait, what?", list what they would \
need to already know in required_context.

For each item return:
- topic: 2-5 short lowercase tags (subject matter, not adjectives)
- role: one of setup, development, punchline, callback, closer, aside
- summary: ONE sentence, third person, describing what happens in the segment
- required_context: free-text prerequisites the viewer must already know; \
[] when the segment is fully self-contained
- energy: 0.0-1.0 — how loud, intense or climactic the delivery reads \
(0.1 quiet aside, 0.5 conversational, 0.9 shouted climax)
- can_open: true only if this works as the very FIRST thing a viewer sees, \
with no prior context at all
- can_end: true only if this lands as a final beat rather than trailing off
- entities: ONLY named things a later clip could call back to — people's \
names, place names used as a running joke, recurring catchphrases. Lowercase, \
verbatim. This is NOT a topic list and NOT keyword extraction. Exclude \
subjects, activities, descriptions, numbers and anything you already put in \
topic. If the segment names nobody and repeats no catchphrase, return []. \
Example: for "Bettina says her husband Arresti wastes money on cooking", \
entities are ["bettina", "arresti"] — NOT ["wastes money", "cooking"]

Return every item you were given, in the same order, echoing its id."""

SCHEMA_HINT = """[
  {"id": "<the id given>", "topic": ["..."],
   "role": "setup|development|punchline|callback|closer|aside",
   "summary": "...", "required_context": ["..."], "energy": 0.0, "can_open": false,
   "can_end": false, "entities": ["..."]}
]"""

# Enough neighbouring transcript to spot an unresolved reference without
# doubling the token cost of a batch.
CONTEXT_CHARS = 200
BATCH_SIZE = 5

_ROLES = {r.value for r in Role}


def enrich_segments(
    segments: list[Segment],
    chat: ChatModel,
    *,
    concurrency: int = 4,
    progress: Callable[[int, int], None] | None = None,
    batch_size: int = BATCH_SIZE,
) -> list[Segment]:
    """Return copies of `segments` with `meta` filled in, in input order."""
    if not segments:
        return []

    context = _context_windows(segments)
    batches = [segments[i : i + batch_size] for i in range(0, len(segments), batch_size)]
    results: dict[str, SegmentMeta] = {}
    failed_batches = 0
    done = 0
    width = max(1, concurrency)

    # Hand the backend a window of prompts at a time and let it parallelise
    # its own way — concurrent HTTP for the gateway, one batched forward pass
    # for a local model. A window rather than everything at once so a long run
    # can report progress; local enrichment of a full archive takes minutes,
    # and a single callback at the end would be no use at all.
    for start in range(0, len(batches), width):
        window = batches[start : start + width]
        replies = chat.chat_json_many(
            [_messages_for(batch, context) for batch in window],
            schema_hint=SCHEMA_HINT,
            concurrency=width,
        )
        for batch, reply in zip(window, replies, strict=True):
            if reply is None:
                # One bad batch must not discard the whole archive. A
                # 727-segment run died at 82% this way and lost everything it
                # had enriched. These segments keep the neutral default and
                # are retried by the next `enrich`, which skips what is done.
                failed_batches += 1
                continue
            results.update(_batch_to_meta(batch, reply))
        done += sum(len(batch) for batch in window)
        if progress is not None:
            progress(done, len(segments))

    if failed_batches:
        logger.warning(
            "%d of %d enrichment batches failed; those segments keep default "
            "metadata and will be retried on the next run",
            failed_batches,
            len(batches),
        )
    return [seg.model_copy(update={"meta": results.get(seg.id, SegmentMeta())}) for seg in segments]


def _messages_for(
    batch: Sequence[Segment], context: dict[str, tuple[str, str]]
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _render_batch(batch, context)},
    ]


def _batch_to_meta(batch: Sequence[Segment], raw: Any) -> dict[str, SegmentMeta]:
    items = _as_items(raw)
    by_id = {str(item.get("id")): item for item in items if isinstance(item, dict)}

    out: dict[str, SegmentMeta] = {}
    for position, seg in enumerate(batch):
        # Prefer the echoed id; fall back to positional alignment when the model
        # dropped or mangled it, so one sloppy field does not cost the batch.
        item = by_id.get(seg.id)
        if item is None and position < len(items) and isinstance(items[position], dict):
            item = items[position]
        out[seg.id] = _to_meta(item)
    return out


def _as_items(raw: Any) -> list[Any]:
    """Accept a bare array or the common `{"segments": [...]}` wrapper."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for value in raw.values():
            if isinstance(value, list):
                return value
        return [raw]
    return []


def _to_meta(item: Any) -> SegmentMeta:
    """Validate one item, falling back to a neutral default rather than raising."""
    if not isinstance(item, dict):
        return SegmentMeta()
    try:
        return SegmentMeta.model_validate(_coerce(item))
    except ValidationError:
        return SegmentMeta()


def _coerce(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise the shapes models reliably get slightly wrong."""
    data = {k: v for k, v in item.items() if k in SegmentMeta.model_fields}
    role = data.get("role")
    if isinstance(role, str):
        role = role.strip().lower()
        # An unknown role should cost only the role, not the whole item.
        data["role"] = role if role in _ROLES else Role.DEVELOPMENT.value
    for field in ("topic", "entities", "required_context"):
        value = data.get(field)
        if isinstance(value, str):
            data[field] = [value] if value.strip() else []
        elif isinstance(value, list):
            data[field] = [str(v).strip() for v in value if str(v).strip()]
    if isinstance(data.get("topic"), list):
        data["topic"] = [t.lower() for t in data["topic"]]
    if isinstance(data.get("entities"), list):
        data["entities"] = [e for e in (e.lower() for e in data["entities"]) if _is_entity(e)]
    return data


# A number is never something a later clip calls back to, but models keep
# offering ages, years and quiz answers as entities. Cheap to reject here, and
# it applies whichever backend produced the item.
_NUMERIC = re.compile(r"^[\d\s.,:%$£€/-]+$")


def _is_entity(value: str) -> bool:
    return bool(value) and not _NUMERIC.match(value)


def _context_windows(segments: Iterable[Segment]) -> dict[str, tuple[str, str]]:
    """Map segment id -> (text before, text after) within the same source."""
    by_source: dict[str, list[Segment]] = {}
    for seg in segments:
        by_source.setdefault(seg.source_id, []).append(seg)

    windows: dict[str, tuple[str, str]] = {}
    for group in by_source.values():
        ordered = sorted(group, key=lambda s: (s.start, s.cue_start))
        for i, seg in enumerate(ordered):
            before = ordered[i - 1].text[-CONTEXT_CHARS:] if i else ""
            after = ordered[i + 1].text[:CONTEXT_CHARS] if i + 1 < len(ordered) else ""
            windows[seg.id] = (before.strip(), after.strip())
    return windows


def _render_batch(batch: Sequence[Segment], context: dict[str, tuple[str, str]]) -> str:
    blocks: list[str] = []
    for n, seg in enumerate(batch, start=1):
        before, after = context.get(seg.id, ("", ""))
        blocks.append(
            f"### ITEM {n}\n"
            f"id: {seg.id}\n"
            f"CONTEXT BEFORE (not part of the segment): {before or '(start of recording)'}\n"
            f"SEGMENT: {seg.text.strip()}\n"
            f"CONTEXT AFTER (not part of the segment): {after or '(end of recording)'}"
        )
    return (
        f"Analyse these {len(batch)} segments. Return a JSON array of "
        f"{len(batch)} objects, one per item, in order.\n\n" + "\n\n".join(blocks)
    )
