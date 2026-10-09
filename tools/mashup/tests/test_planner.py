from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import make_segment, unit_vec

from mashup.models import Role
from mashup.plan.planner import (
    UNFINISHED_PENALTY,
    ending_penalty,
    plan,
    plan_random,
    plan_semantic,
    rescore,
)
from mashup.plan.score import PlanContext
from mashup.retrieve import Candidate


def build_pool(n: int = 12) -> list[Candidate]:
    """A pool spread across episodes, energies, and roles."""
    segs = []
    for i in range(n):
        segs.append(
            make_segment(
                f"s{i:02d}",
                source_id=f"ep{i % 4:02d}",
                start=float(i) * 100,
                duration=45.0,
                angle=0.02 * i,
                energy=0.1 + (i % 5) * 0.2,
                role=[Role.SETUP, Role.DEVELOPMENT, Role.PUNCHLINE, Role.CLOSER][i % 4],
                topics=[f"topic{i % 3}"],
                entities=[f"gag{i % 3}"],
                can_open=(i % 4 == 0),
                can_end=(i % 4 == 3),
            )
        )
    return [Candidate(segment=s, relevance=1.0 - 0.02 * i) for i, s in enumerate(segs)]


def ctx(target: float = 270.0) -> PlanContext:
    return PlanContext(
        query_vec=unit_vec(0.0),
        target_duration=target,
        source_ordinals={f"ep{i:02d}": i for i in range(4)},
    )


def test_plan_hits_duration_target(cosine_sim):
    result = plan("escalation", build_pool(), ctx(270.0), cosine_sim)
    total = sum(s.duration for s in result.sequence)
    assert 200 <= total <= 300, total


def test_plan_never_repeats_a_segment(cosine_sim):
    result = plan("callback", build_pool(), ctx(), cosine_sim)
    ids = [s.id for s in result.sequence]
    assert len(ids) == len(set(ids))


def test_plan_supports_a_seven_minute_unique_edit(cosine_sim):
    target = 420.0
    result = plan("escalation", build_pool(16), ctx(target), cosine_sim)
    total = sum(segment.duration for segment in result.sequence)
    material = [member for segment in result.sequence for member in segment.material_ids]

    assert target * (1 - 0.06) <= total <= target * 1.10
    assert len(material) == len(set(material))


def test_chronological_respects_archive_order(cosine_sim):
    c = ctx()
    result = plan("chronological", build_pool(), c, cosine_sim)
    keys = [(c.source_ordinals[s.source_id], s.start) for s in result.sequence]
    assert keys == sorted(keys), "chronological must not reorder the archive"


def test_non_chronological_strategies_may_reorder(cosine_sim):
    c = ctx()
    result = plan("escalation", build_pool(24), c, cosine_sim)
    keys = [(c.source_ordinals[s.source_id], s.start) for s in result.sequence]
    # Not a guarantee for any single pool, but with 24 varied candidates an
    # escalation-weighted objective should not coincidentally pick archive order.
    assert keys != sorted(keys)


def test_plan_prefers_an_opener_first(cosine_sim):
    result = plan("escalation", build_pool(), ctx(), cosine_sim)
    assert result.sequence[0].meta.can_open


def test_escalation_beats_chronological_on_its_own_objective(cosine_sim):
    pool, c = build_pool(16), ctx()
    from mashup.plan.score import score_sequence, total_score

    esc = plan("escalation", pool, c, cosine_sim)
    chrono = plan("chronological", pool, c, cosine_sim)
    esc_on_esc = total_score(score_sequence(esc.sequence, c, cosine_sim), "escalation")
    chrono_on_esc = total_score(score_sequence(chrono.sequence, c, cosine_sim), "escalation")
    assert esc_on_esc >= chrono_on_esc


def test_unknown_strategy_rejected(cosine_sim):
    with pytest.raises(ValueError, match="unknown strategy"):
        plan("vibes", build_pool(), ctx(), cosine_sim)


def test_empty_candidates_rejected(cosine_sim):
    with pytest.raises(ValueError, match="no candidates"):
        plan("escalation", [], ctx(), cosine_sim)


def test_semantic_baseline_is_relevance_ordered(cosine_sim):
    pool = build_pool()
    result = plan_semantic(pool, ctx(), cosine_sim)
    order = [s.id for s in result.sequence]
    expected = [c.segment.id for c in sorted(pool, key=lambda c: c.relevance, reverse=True)]
    assert order == expected[: len(order)]


def test_random_baseline_is_deterministic_per_seed(cosine_sim):
    pool, c = build_pool(), ctx()
    a = plan_random(pool, c, cosine_sim, seed=7)
    b = plan_random(pool, c, cosine_sim, seed=7)
    assert [s.id for s in a.sequence] == [s.id for s in b.sequence]


def test_baselines_respect_duration_ceiling(cosine_sim):
    for result in (
        plan_semantic(build_pool(), ctx(200.0), cosine_sim),
        plan_random(build_pool(), ctx(200.0), cosine_sim),
    ):
        assert sum(s.duration for s in result.sequence) <= 220.0


def test_rescore_reflects_an_edit(cosine_sim):
    c = ctx()
    result = plan("escalation", build_pool(), c, cosine_sim)
    trimmed = result
    trimmed.sequence = result.sequence[:2]
    after = rescore(trimmed, c, cosine_sim)
    assert after.score != pytest.approx(result.score)


def test_rescore_charges_the_same_ending_penalty_as_plan(cosine_sim):
    """`plan` used to apply the unfinished-ending penalty and `rescore` not
    to, so the same clips scored 6% higher whenever they came back through
    rescore. The matched-pair experiment compares one clip set in two orders
    across exactly those two functions, and the gap alone was enough to make
    an arbitrary shuffle outrank the planner's own output."""
    c = ctx()
    planned = plan("escalation", build_pool(), c, cosine_sim)
    assert rescore(planned, c, cosine_sim).score == pytest.approx(planned.score)


def test_the_ending_penalty_is_the_only_gap_between_two_identical_orders(cosine_sim):
    """Two sequences with the same clips in the same order, differing only in
    whether the last clip is flagged `can_end`, must differ by exactly the
    penalty. This is what makes a matched pair's two arms comparable."""
    c = ctx()
    closer = make_segment("z", source_id="ep03", angle=0.05, can_end=True)
    opener = make_segment("z", source_id="ep03", angle=0.05, can_end=False)
    body = [s.segment for s in build_pool()[:3]]

    base = plan("escalation", build_pool(), c, cosine_sim)
    ends_well = rescore(replace(base, sequence=[*body, closer]), c, cosine_sim)
    ends_badly = rescore(replace(base, sequence=[*body, opener]), c, cosine_sim)

    # Same clips, same order, same terms — only the ending flag differs.
    assert ends_badly.terms == ends_well.terms
    assert ends_badly.score == pytest.approx(ends_well.score * UNFINISHED_PENALTY)


def test_ending_penalty_is_only_charged_when_the_clip_cannot_end():
    can = make_segment("yes", can_end=True)
    cannot = make_segment("no", can_end=False)
    assert ending_penalty([can]) == 1.0
    assert ending_penalty([cannot]) == UNFINISHED_PENALTY
    assert ending_penalty([]) == 1.0


def test_rationale_is_populated(cosine_sim):
    result = plan("callback", build_pool(), ctx(), cosine_sim)
    assert result.rationale
    assert any("clips" in line for line in result.rationale)
