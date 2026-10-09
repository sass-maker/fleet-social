from __future__ import annotations

import pytest

from mashup.content_integrity import remove_snapped_render_overlap, validate_unique_content
from mashup.models import Clip, Role


def _clip(
    index: int,
    segment_id: str,
    *,
    start: float,
    end: float,
    render_start: float,
    render_end: float,
) -> Clip:
    return Clip(
        index=index,
        segment_id=segment_id,
        segment_ids=[segment_id],
        source_id="episode-1",
        source_title="Episode 1",
        source_path="/owned/episode-1.mp3",
        start=start,
        end=end,
        render_start=render_start,
        render_end=render_end,
        text=f"Material {segment_id}",
        summary=f"Summary {segment_id}",
        role=Role.DEVELOPMENT,
        energy=0.5,
    )


def test_rejects_repeated_member_material() -> None:
    clips = [
        _clip(0, "segment-a", start=0, end=10, render_start=0, render_end=10),
        _clip(1, "segment-a", start=20, end=30, render_start=20, render_end=30),
    ]
    with pytest.raises(ValueError, match="repeat material id segment-a"):
        validate_unique_content(clips)


def test_rejects_overlapping_planned_source_audio() -> None:
    clips = [
        _clip(0, "segment-a", start=0, end=12, render_start=0, render_end=12),
        _clip(1, "segment-b", start=10, end=20, render_start=10, render_end=20),
    ]
    with pytest.raises(ValueError, match="overlapping planned audio"):
        validate_unique_content(clips)


def test_trims_snapped_handles_without_removing_planned_material() -> None:
    clips = [
        _clip(0, "segment-a", start=0, end=10, render_start=0, render_end=11),
        _clip(1, "segment-b", start=11, end=20, render_start=9.5, render_end=20),
    ]
    clean = remove_snapped_render_overlap(clips)

    assert clean[0].render_end == pytest.approx(10.5)
    assert clean[1].render_start == pytest.approx(10.5)
    assert clean[0].render_end >= clean[0].end
    assert clean[1].render_start <= clean[1].start
    validate_unique_content(clean)
