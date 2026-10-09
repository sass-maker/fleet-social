"""Hard guarantees against replaying the same source material."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from mashup.models import Clip

_EPSILON = 1e-6


def _reject_overlaps(clips: list[Clip], *, start_field: str, end_field: str) -> None:
    by_source: dict[str, list[Clip]] = defaultdict(list)
    for clip in clips:
        by_source[clip.source_id].append(clip)

    label = "planned" if start_field == "start" else "rendered"
    for source_id, source_clips in by_source.items():
        ordered = sorted(source_clips, key=lambda clip: getattr(clip, start_field))
        furthest: Clip | None = None
        furthest_end = float("-inf")
        for clip in ordered:
            start = getattr(clip, start_field)
            if furthest is not None and start < furthest_end - _EPSILON:
                raise ValueError(
                    f"clips {furthest.index} and {clip.index} replay overlapping "
                    f"{label} audio from source {source_id}"
                )
            end = getattr(clip, end_field)
            if end > furthest_end:
                furthest = clip
                furthest_end = end


def validate_unique_content(clips: Iterable[Clip]) -> None:
    """Reject repeated stored material and overlapping source-audio intervals."""
    rows = list(clips)
    owners: dict[str, int] = {}
    for clip in rows:
        if len(clip.segment_ids) != len(set(clip.segment_ids)):
            raise ValueError(f"clip {clip.index} repeats a material id internally")
        for material_id in {clip.segment_id, *clip.segment_ids}:
            previous = owners.get(material_id)
            if previous is not None:
                raise ValueError(
                    f"clips {previous} and {clip.index} repeat material id {material_id}"
                )
            owners[material_id] = clip.index
    _reject_overlaps(rows, start_field="start", end_field="end")
    _reject_overlaps(rows, start_field="render_start", end_field="render_end")


def remove_snapped_render_overlap(clips: Iterable[Clip]) -> list[Clip]:
    """Trim boundary handles so distinct planned ranges never replay audio.

    Boundary snapping widens clips to nearby silence. Adjacent clips from the
    same source can therefore acquire overlapping render handles even though
    their planned material is disjoint. The midpoint of the planned gap is a
    safe shared boundary: it keeps both planned ranges and removes replay.
    """
    rows = list(clips)
    _reject_overlaps(rows, start_field="start", end_field="end")

    by_source: dict[str, list[int]] = defaultdict(list)
    for position, clip in enumerate(rows):
        by_source[clip.source_id].append(position)

    for positions in by_source.values():
        positions.sort(key=lambda position: rows[position].start)
        for left_position, right_position in zip(positions, positions[1:], strict=False):
            left = rows[left_position]
            right = rows[right_position]
            if left.render_end <= right.render_start + _EPSILON:
                continue
            boundary = (left.end + right.start) / 2
            rows[left_position] = left.model_copy(update={"render_end": boundary})
            rows[right_position] = right.model_copy(update={"render_start": boundary})

    validate_unique_content(rows)
    return rows
