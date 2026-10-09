from __future__ import annotations

import json
import re

import pytest
from conftest import make_segment

from mashup.models import EDL, Clip, Cue, Role
from mashup.retrieve import Candidate
from mashup.shorts import (
    attach_visual_manifest,
    build_short_candidates,
    cue_window_candidates,
    review_short_candidates,
    select_distinct_segments,
    validate_short_duration,
)


def cues() -> list[Cue]:
    lines = [
        "And this is earlier context.",
        "So this still needs prior context.",
        "What should founders believe?",
        "The answer starts with conviction.",
        "Chasing money is understandable.",
        "A product needs a stronger center.",
        "Use the thing instead of following narratives.",
        "Talk to real people.",
        "Ask better questions.",
        "Make one deliberate trade-off.",
        "Stay curious.",
        "And this continues elsewhere.",
    ]
    return [
        Cue(index=index, start=index * 5.0, end=(index + 1) * 5.0, text=text)
        for index, text in enumerate(lines)
    ]


def test_cue_window_is_contiguous_complete_and_target_sized():
    anchor_segment = make_segment(
        "anchor",
        start=20,
        duration=25,
        text="anchor words",
        can_open=False,
        can_end=False,
    ).model_copy(update={"cue_start": 4, "cue_end": 8})
    windows = cue_window_candidates(
        Candidate(segment=anchor_segment, relevance=0.9),
        cues(),
        target=45,
    )

    assert windows
    selected = windows[0].segment
    assert (selected.cue_start, selected.cue_end) == (2, 10)
    assert selected.duration == 45
    assert selected.text == " ".join(cue.text for cue in cues()[2:11])
    assert not selected.meta.can_open and not selected.meta.can_end
    assert selected.anchor_segment_id == "anchor"


def test_dialogue_marker_does_not_hide_a_continuation_opening():
    from mashup.shorts import _ends_cleanly, _starts_cleanly

    assert not _starts_cleanly(">> So this still needs prior context.")
    assert _starts_cleanly(">> What should founders believe?")
    assert not _ends_cleanly(">> Exactly. Yeah.")
    assert _ends_cleanly(">> I am not afraid of rejection.")


class ReviewChat:
    name = "test:short-review"

    def __init__(self, *, payoff: float = 0.8) -> None:
        self.payoff = payoff
        self.calls = 0

    def chat_json_many(self, conversations, *, schema_hint, concurrency=4):
        assert "payoff_strength" in schema_hint
        self.calls += len(conversations)
        replies = []
        for messages in conversations:
            ids = re.findall(r"^id: (\S+)$", messages[-1]["content"], re.MULTILINE)
            windows = re.findall(
                r"EXACT WINDOW: (.*?)\nCONTEXT AFTER", messages[-1]["content"], re.DOTALL
            )
            replies.append(
                [
                    {
                        "id": item_id,
                        "can_open": True,
                        "can_end": True,
                        "opening_quote": " ".join(window.split()[:4]),
                        "ending_quote": " ".join(window.split()[-4:]),
                        "hook_strength": 0.75,
                        "payoff_strength": self.payoff,
                        "reason": "The exact window opens cold and lands a takeaway.",
                    }
                    for item_id, window in zip(ids, windows, strict=True)
                ]
            )
        return replies


def test_short_review_is_cached_and_makes_completion_claims_honest(tmp_path):
    anchor = make_segment(
        "anchor",
        start=20,
        duration=25,
        can_open=False,
        can_end=False,
    ).model_copy(update={"cue_start": 4, "cue_end": 8})
    candidates = build_short_candidates(
        [Candidate(segment=anchor, relevance=0.9)],
        {"ep01": cues()},
        target=45,
    )
    chat = ReviewChat()

    first = review_short_candidates(candidates, {"ep01": cues()}, chat, tmp_path)
    calls = chat.calls
    second = review_short_candidates(candidates, {"ep01": cues()}, chat, tmp_path)

    assert first and second
    assert chat.calls == calls
    assert first[0].candidate.segment.meta.can_open
    assert first[0].candidate.segment.meta.can_end
    assert first[0].review.payoff_strength == 0.8


def test_short_review_rejects_a_window_without_payoff(tmp_path):
    anchor = make_segment("anchor", start=20, duration=25).model_copy(
        update={"cue_start": 4, "cue_end": 8}
    )
    candidates = build_short_candidates(
        [Candidate(segment=anchor, relevance=0.9)],
        {"ep01": cues()},
        target=45,
    )

    reviewed = review_short_candidates(
        candidates,
        {"ep01": cues()},
        ReviewChat(payoff=0.3),
        tmp_path,
    )

    assert reviewed == []


def test_short_review_rejects_hallucinated_boundary_quotes(tmp_path):
    anchor = make_segment("anchor", start=20, duration=25).model_copy(
        update={"cue_start": 4, "cue_end": 8}
    )
    candidates = build_short_candidates(
        [Candidate(segment=anchor, relevance=0.9)],
        {"ep01": cues()},
        target=45,
    )
    chat = ReviewChat()
    original = chat.chat_json_many

    def hallucinating(conversations, *, schema_hint, concurrency=4):
        replies = original(conversations, schema_hint=schema_hint, concurrency=concurrency)
        for reply in replies:
            for item in reply:
                item["ending_quote"] = "a stronger invented conclusion"
        return replies

    chat.chat_json_many = hallucinating

    reviewed = review_short_candidates(candidates, {"ep01": cues()}, chat, tmp_path)

    assert reviewed == []


def test_short_candidates_reject_missing_clean_windows():
    anchor = Candidate(segment=make_segment("anchor", start=0, duration=10), relevance=0.8)
    with pytest.raises(ValueError, match="no retrieved anchor"):
        build_short_candidates([anchor], {"ep01": cues()[:2]}, target=45)


def test_distinct_short_batch_rejects_overlapping_source_windows():
    ranked = [
        make_segment("a", source_id="ep1", start=0, duration=45),
        make_segment("b", source_id="ep1", start=20, duration=45),
        make_segment("c", source_id="ep1", start=60, duration=45),
        make_segment("d", source_id="ep2", start=0, duration=45),
    ]

    selected = select_distinct_segments(ranked, count=3)

    assert [segment.id for segment in selected] == ["a", "c", "d"]


def test_distinct_short_batch_reports_shortfall():
    ranked = [
        make_segment("a", source_id="ep1", start=0, duration=45),
        make_segment("b", source_id="ep1", start=10, duration=45),
    ]

    with pytest.raises(ValueError, match="produced 1 distinct.*3 requested"):
        select_distinct_segments(ranked, count=3)


@pytest.mark.parametrize("target", [0, 29.9, 60.1, 90])
def test_short_duration_rejects_values_outside_social_range(target):
    with pytest.raises(ValueError, match="between 30 and 60"):
        validate_short_duration(target)


def _edl(source_path: str) -> EDL:
    return EDL(
        strategy="short",
        prompt="test",
        target_duration=45,
        generated_at="2026-07-29T00:00:00Z",
        clips=[
            Clip(
                index=0,
                segment_id="segment",
                source_id="episode",
                source_title="Episode",
                source_path=source_path,
                start=0,
                end=45,
                render_start=0,
                render_end=45,
                text="spoken source",
                summary="summary",
                role=Role.CLOSER,
                energy=0.8,
            )
        ],
    )


def test_visual_manifest_is_validated_and_persisted(tmp_path):
    spoken = tmp_path / "spoken.mp3"
    image = tmp_path / "archive.mp4"
    spoken.write_bytes(b"spoken")
    image.write_bytes(b"archive")
    manifest = tmp_path / "visuals.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "clip_index": 0,
                    "mode": "motion",
                    "start": 8,
                    "end": 14,
                    "source_path": str(image),
                    "source_time": 12.5,
                    "source_title": "Public Domain Collection",
                    "source_url": "https://example.test/archive",
                }
            ]
        )
    )

    attached = attach_visual_manifest(_edl(str(spoken)), manifest)

    assert len(attached.clips[0].visuals) == 1
    assert attached.clips[0].visuals[0].source_path == str(image.resolve())
    assert attached.clips[0].visuals[0].source_time == 12.5
    assert attached.clips[0].visuals[0].mode == "motion"


def test_visual_mode_defaults_to_still_for_legacy_manifests(tmp_path):
    spoken = tmp_path / "spoken.mp3"
    image = tmp_path / "archive.mp4"
    spoken.write_bytes(b"spoken")
    image.write_bytes(b"archive")
    manifest = tmp_path / "visuals.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "clip_index": 0,
                    "start": 8,
                    "end": 14,
                    "source_path": str(image),
                    "source_title": "Public Domain Collection",
                }
            ]
        )
    )

    attached = attach_visual_manifest(_edl(str(spoken)), manifest)

    assert attached.clips[0].visuals[0].mode == "still"


def test_visual_manifest_rejects_interval_outside_clip(tmp_path):
    spoken = tmp_path / "spoken.mp3"
    image = tmp_path / "archive.mp4"
    spoken.write_bytes(b"spoken")
    image.write_bytes(b"archive")
    manifest = tmp_path / "visuals.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "clip_index": 0,
                    "start": 40,
                    "end": 50,
                    "source_path": str(image),
                    "source_title": "Public Domain Collection",
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="clip 0 is 45.00s"):
        attach_visual_manifest(_edl(str(spoken)), manifest)
