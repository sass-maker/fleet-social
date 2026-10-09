from __future__ import annotations

import re

import pytest
from conftest import make_segment, unit_vec

from mashup.models import EDL, Clip, Role
from mashup.plan.planner import plan_semantic
from mashup.plan.score import PlanContext
from mashup.retrieve import Candidate, Retriever
from mashup.segment.editorial import (
    BoundaryReview,
    EditorialIntegrityError,
    build_editorial_candidates,
    review_candidate_boundaries,
)


def candidates(*segments) -> list[Candidate]:
    return [Candidate(segment=segment, relevance=1.0) for segment in segments]


def test_repairs_an_anchor_with_the_smallest_clean_span():
    opener = make_segment("s0", start=0, duration=40, can_open=True, text="Here is the setup.")
    anchor = make_segment(
        "s1",
        start=40,
        duration=40,
        required_context=["the setup"],
        text="The central point.",
    )
    closer = make_segment("s2", start=80, duration=40, can_end=True, text="That is the landing.")

    repaired = build_editorial_candidates(
        candidates(anchor), [opener, anchor, closer], unit_vec(0.0)
    )

    assert len(repaired) == 1
    bit = repaired[0].segment
    assert bit.member_segment_ids == ["s0", "s1", "s2"]
    assert bit.anchor_segment_id == "s1"
    assert (bit.start, bit.end) == (0, 120)
    assert bit.meta.can_open is True
    assert bit.meta.can_end is True
    assert bit.meta.required_context == []
    assert bit.text == "Here is the setup. The central point. That is the landing."


def test_deduplicates_the_same_span_from_multiple_anchors():
    opener = make_segment("s0", start=0, can_open=True, text="A complete opening.")
    middle = make_segment("s1", start=60)
    closer = make_segment("s2", start=120, can_end=True, text="A clean ending.")

    repaired = build_editorial_candidates(
        candidates(opener, middle, closer),
        [opener, middle, closer],
        unit_vec(0.0),
    )

    assert [candidate.segment.member_segment_ids for candidate in repaired] == [["s0", "s1", "s2"]]


def test_rejects_a_pool_without_a_clean_landing():
    opener = make_segment("s0", can_open=True)
    fragment = make_segment("s1", start=60)

    with pytest.raises(EditorialIntegrityError, match="rejected all 1 AI anchors"):
        build_editorial_candidates(
            candidates(fragment),
            [opener, fragment],
            unit_vec(0.0),
            label="AI",
        )


def test_candidate_review_overrides_stale_enrichment_flags():
    opener = make_segment("s0", start=0, text="A complete opening.")
    anchor = make_segment("s1", start=60)
    closer = make_segment("s2", start=120, text="A clean ending.")
    reviews = {
        "s0": BoundaryReview(can_open=True),
        "s1": BoundaryReview(),
        "s2": BoundaryReview(can_end=True),
    }

    repaired = build_editorial_candidates(
        candidates(anchor),
        [opener, anchor, closer],
        unit_vec(0.0),
        reviews=reviews,
    )

    assert repaired[0].segment.member_segment_ids == ["s0", "s1", "s2"]


@pytest.mark.parametrize(
    "opening",
    [
        "yeah, I mean this was difficult.",
        "is you had to have a middleman.",
        "Three when do I actually need to do it?",
        "It just gives us some insight.",
    ],
)
def test_obvious_conversational_continuations_veto_model_approval(opening):
    start = make_segment("s0", text=opening)
    anchor = make_segment("s1", start=60, can_end=True)
    reviews = {
        "s0": BoundaryReview(can_open=True),
        "s1": BoundaryReview(can_end=True),
    }

    with pytest.raises(EditorialIntegrityError):
        build_editorial_candidates(
            candidates(anchor),
            [start, anchor],
            unit_vec(0.0),
            reviews=reviews,
        )


class StubBoundaryChat:
    name = "stub:boundary"

    def __init__(self) -> None:
        self.calls = 0

    def chat_json_many(self, conversations, **_kwargs):
        self.calls += 1
        replies = []
        for messages in conversations:
            ids = re.findall(r"^id: (.+)$", messages[-1]["content"], re.MULTILINE)
            replies.append(
                [
                    {
                        "id": segment_id,
                        "can_open": segment_id == "s0",
                        "can_end": segment_id == "s2",
                        "required_context": [],
                        "reason": "stub",
                    }
                    for segment_id in ids
                ]
            )
        return replies


def test_candidate_reviews_are_content_cached(tmp_path):
    archive = [
        make_segment("s0", start=0),
        make_segment("s1", start=60),
        make_segment("s2", start=120),
    ]
    chat = StubBoundaryChat()

    first = review_candidate_boundaries(candidates(archive[1]), archive, chat, tmp_path)
    calls = chat.calls
    second = review_candidate_boundaries(candidates(archive[1]), archive, chat, tmp_path)

    assert first == second
    assert set(first) == {"s0", "s1", "s2"}
    assert calls > 0
    assert chat.calls == calls


def test_repair_limits_prevent_an_unbounded_bit():
    opener = make_segment("s0", start=0, can_open=True)
    middle = [make_segment(f"s{i}", start=i * 60) for i in range(1, 5)]
    closer = make_segment("s5", start=300, can_end=True)

    with pytest.raises(EditorialIntegrityError):
        build_editorial_candidates(
            candidates(middle[1]),
            [opener, *middle, closer],
            unit_vec(0.0),
            max_members=4,
            max_duration=240,
        )


def test_transient_bits_use_direct_embedding_similarity():
    opener = make_segment("s0", start=0, can_open=True, angle=0.0, text="A complete opening.")
    closer = make_segment("s1", start=60, can_end=True, angle=0.1, text="A clean ending.")
    retriever = Retriever([opener, closer])
    bit = build_editorial_candidates(candidates(opener), [opener, closer], unit_vec(0.0))[0].segment

    assert retriever.pairwise(bit, closer) > 0.9


def test_semantic_baseline_excludes_overlapping_bits():
    a = make_segment("a", duration=120, can_open=True, can_end=True)
    b = make_segment("b", start=120, duration=120, can_open=True, can_end=True)
    c = make_segment("c", start=240, duration=120, can_open=True, can_end=True)
    left = a.model_copy(update={"id": "bit-left", "member_segment_ids": ["a", "b"]})
    right = b.model_copy(update={"id": "bit-right", "member_segment_ids": ["b", "c"]})
    independent = c.model_copy(update={"id": "bit-c", "member_segment_ids": ["c"]})
    pool = [
        Candidate(segment=left, relevance=1.0),
        Candidate(segment=right, relevance=0.9),
        Candidate(segment=independent, relevance=0.8),
    ]
    ctx = PlanContext(query_vec=unit_vec(0.0), target_duration=400)

    result = plan_semantic(pool, ctx, lambda _a, _b: 0.0)

    used = [segment.material_ids for segment in result.sequence]
    assert used == [frozenset({"a", "b"}), frozenset({"c"})]


def test_legacy_clip_treats_segment_id_as_its_only_member():
    clip = Clip(
        index=0,
        segment_id="stored",
        source_id="episode",
        source_path="/tmp/episode.mp4",
        start=0,
        end=10,
        render_start=0,
        render_end=10,
        text="A complete thought.",
        summary="thought",
        role=Role.SETUP,
        energy=0.5,
    )
    edl = EDL(
        strategy="semantic",
        prompt="thoughts",
        target_duration=10,
        generated_at="2026-07-29T00:00:00Z",
        clips=[clip],
    )

    assert edl.clips[0].segment_ids == []
