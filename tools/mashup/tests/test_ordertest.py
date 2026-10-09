"""The mechanical proxy for the blind study.

Shuffling a clip set and seeing where the planner's own order lands is the
cheapest available check on the project's claim, and the one that decides
whether six people's time is worth spending. Its arithmetic is worth testing
more carefully than the code it grades.
"""

from __future__ import annotations

import pytest
from test_planner import build_pool, ctx

from mashup.models import EDL, ScoreTerms
from mashup.ordertest import (
    CONFIDENT_PERCENTILE,
    ORDER_INVARIANT,
    ORDER_SENSITIVE,
    Null,
    StudyAudit,
    best_of,
    null_distribution,
)
from mashup.plan.planner import plan
from mashup.plan.score import WEIGHT_PROFILES


def _null(actual: float, scores: list[float], strategy: str = "escalation") -> Null:
    return Null(strategy=strategy, clips=len(scores), actual=actual, scores=scores)


# ---- the summary statistics ---------------------------------------------


def test_percentile_is_the_share_of_shuffles_beaten():
    null = _null(0.8, [0.1, 0.2, 0.9, 1.0])
    assert null.percentile == 50.0


def test_an_order_beating_nothing_reads_zero():
    assert _null(0.0, [0.1, 0.2, 0.3]).percentile == 0.0


def test_gap_is_measured_against_the_median_not_the_worst():
    """Against the worst shuffle everything looks good. The claim is that the
    planner beats a *typical* arbitrary order."""
    null = _null(0.6, [0.1, 0.5, 0.5, 0.55])
    assert null.median == pytest.approx(0.5)
    assert null.gap == pytest.approx(0.1)


def test_confidence_tracks_the_stated_threshold():
    assert _null(1.0, [0.0] * 100).confident
    assert not _null(0.0, [1.0] * 100).confident
    borderline = _null(0.5, [0.0] * 75 + [1.0] * 25)
    assert borderline.percentile == pytest.approx(CONFIDENT_PERCENTILE)
    assert borderline.confident


def test_empty_distribution_does_not_divide_by_zero():
    null = _null(0.5, [])
    assert null.percentile == 0.0
    assert null.median == 0.0
    assert not null.confident


def test_the_two_term_groups_partition_the_objective():
    """If a term were in neither list, order_blind_weight would understate how
    much of the objective a shuffle cannot touch."""
    for profile in WEIGHT_PROFILES.values():
        assert set(ORDER_INVARIANT) | set(ORDER_SENSITIVE) == set(profile)
    assert not set(ORDER_INVARIANT) & set(ORDER_SENSITIVE)


def test_baseline_profile_is_entirely_order_blind():
    """The baselines are scored on relevance and duration, both invariant under
    reordering. Any null spread they show is the ending penalty alone."""
    assert _null(0.5, [0.5], strategy="semantic").order_blind_weight() == pytest.approx(1.0)
    assert _null(0.5, [0.5], strategy="random").order_blind_weight() == pytest.approx(1.0)


def test_ai_profiles_can_see_at_least_some_order():
    for strategy in ("chronological", "escalation", "callback"):
        assert _null(0.5, [0.5], strategy=strategy).order_blind_weight() < 1.0


# ---- the distribution itself --------------------------------------------


def test_order_invariant_terms_do_not_move_under_shuffling(cosine_sim):
    """The premise of the whole test. If these moved, a percentile would be
    measuring selection rather than ordering."""
    c = ctx()
    seq = [cand.segment for cand in build_pool()[:6]]
    from mashup.plan.score import score_sequence

    base = score_sequence(seq, c, cosine_sim)
    shuffled = score_sequence([*seq[3:], *seq[:3]], c, cosine_sim)
    for term in ORDER_INVARIANT:
        assert getattr(base, term) == pytest.approx(getattr(shuffled, term))


def test_distribution_is_deterministic_per_seed(cosine_sim):
    c = ctx()
    seq = [cand.segment for cand in build_pool()[:6]]
    kwargs = dict(strategy="escalation", actual=0.5, shuffles=20, seed=3)
    a = null_distribution(seq, c, cosine_sim, **kwargs)
    b = null_distribution(seq, c, cosine_sim, **kwargs)
    assert a.scores == b.scores


def test_a_planned_order_beats_most_shuffles_of_its_own_clips(cosine_sim):
    """The encouraging half of the audit: the beam search really is optimising
    order rather than dressing up a retrieval result."""
    c = ctx()
    result = plan("escalation", build_pool(16), c, cosine_sim)
    null = null_distribution(
        result.sequence,
        c,
        cosine_sim,
        strategy="escalation",
        actual=result.score,
        shuffles=200,
    )
    assert null.percentile >= 75.0, f"planner order only beat {null.percentile:.0f}% of shuffles"


def test_shuffle_count_is_honoured(cosine_sim):
    c = ctx()
    seq = [cand.segment for cand in build_pool()[:5]]
    null = null_distribution(seq, c, cosine_sim, strategy="escalation", actual=0.5, shuffles=7)
    assert len(null.scores) == 7


# ---- choosing a configuration -------------------------------------------


def test_best_of_prefers_the_higher_percentile():
    lo = (40, _null(0.9, [0.0] * 50 + [1.0] * 50))  # 50th
    hi = (80, _null(0.9, [0.0] * 100))  # 100th
    assert best_of([lo, hi]) == hi


def test_best_of_breaks_a_percentile_tie_on_the_gap():
    """Two configurations both ahead of every shuffle are not equally
    convincing; the one further clear of a typical order is."""
    narrow = (40, _null(0.51, [0.5] * 100))
    wide = (80, _null(0.90, [0.5] * 100))
    assert best_of([narrow, wide]) == wide


def test_best_of_handles_no_results():
    assert best_of([]) is None


# ---- auditing an existing set -------------------------------------------


def _edl(strategy: str, terms: ScoreTerms) -> EDL:
    return EDL(
        strategy=strategy,
        prompt="p",
        target_duration=100.0,
        generated_at="2026-07-26T00:00:00+00:00",
        clips=[],
        score=0.5,
        terms=terms,
        weights={},
        rationale=[],
    )


def _audit(overlap: dict, strategies=("chronological", "escalation")) -> StudyAudit:
    return StudyAudit(
        prompt="p",
        variants={s: _edl(s, ScoreTerms()) for s in strategies},
        overlap=overlap,
        nulls={s: _null(0.5, [0.5], strategy=s) for s in strategies},
    )


def test_isolated_names_a_variant_sharing_nothing_with_the_others():
    """The maximum overlap across a set is the wrong summary. On the dev
    archive callback and escalation shared 0.54 while chronological sat at
    0.05 against all four others; keying off the max hid exactly the variant
    that made the study uninterpretable."""
    audit = _audit(
        {
            ("callback", "escalation"): 0.54,
            ("chronological", "callback"): 0.05,
            ("chronological", "escalation"): 0.02,
        },
        strategies=("chronological", "escalation", "callback"),
    )
    assert [name for name, _ in audit.isolated()] == ["chronological"]


def test_a_matched_pair_is_not_flagged_as_isolated():
    audit = _audit({("chronological", "escalation"): 1.0})
    assert audit.isolated() == []
    assert audit.is_matched()


def test_a_set_with_different_clips_is_not_matched():
    assert not _audit({("chronological", "escalation"): 0.31}).is_matched()


def test_order_blind_conditions_are_named():
    audit = _audit({("semantic", "escalation"): 0.3}, strategies=("semantic", "escalation"))
    assert audit.order_blind() == ["semantic"]


def test_dead_terms_finds_the_ones_that_do_not_discriminate():
    audit = StudyAudit(
        prompt="p",
        variants={
            "a": _edl("escalation", ScoreTerms(relevance=0.40, escalation=0.10)),
            "b": _edl("callback", ScoreTerms(relevance=0.42, escalation=0.90)),
        },
        overlap={("a", "b"): 0.2},
        nulls={},
    )
    dead = dict(audit.dead_terms())
    assert "relevance" in dead
    assert "escalation" not in dead
