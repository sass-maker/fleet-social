"""Threshold calibration.

A fixed cosine threshold is a statement about one embedding model's
similarity scale. Swapping the encoder moved this archive's pairwise
distribution enough that the 0.82 redundancy cut stopped firing at all, so
`non_repetition` returned 1.00 for every sequence — a term that had stopped
measuring anything while still looking healthy in the breakdown. These tests
pin the behaviour that prevents a repeat.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from conftest import make_segment

from mashup.plan.score import (
    MIN_CALIBRATION_SEGMENTS,
    Calibration,
    PlanContext,
    _unit,
    prepare_context,
    term_non_repetition,
    term_progression,
)


def corpus(n: int, *, spread: float = 1.0) -> list:
    """`n` segments fanned across an arc, so similarity varies smoothly."""
    return [make_segment(f"s{i:03d}", angle=spread * i / n) for i in range(n)]


def scaled_corpus(n: int, ceiling: float) -> list:
    """A corpus whose pairwise similarities all sit below `ceiling`."""
    segments = corpus(n, spread=math.acos(max(-1.0, min(1.0, ceiling))) * 2)
    return segments


# ---- deriving thresholds -------------------------------------------------


def test_a_tiny_corpus_keeps_the_fixed_fallbacks() -> None:
    """Percentiles over a handful of vectors are noise, not calibration."""
    calibrated = Calibration.from_corpus(corpus(MIN_CALIBRATION_SEGMENTS - 1))
    assert calibrated == Calibration()
    assert calibrated.source == "default"


def test_a_real_corpus_is_measured() -> None:
    calibrated = Calibration.from_corpus(corpus(40))
    assert calibrated.source == "corpus:40"
    assert calibrated.flow_low < calibrated.flow_high
    assert calibrated.redundancy > calibrated.flow_high


def test_thresholds_track_the_corpus_rather_than_a_constant() -> None:
    """The whole point: a corpus whose vectors are bunched together must get
    a higher redundancy cut than one whose vectors are spread out."""
    tight = Calibration.from_corpus(corpus(40, spread=0.3))
    loose = Calibration.from_corpus(corpus(40, spread=3.0))
    assert tight.redundancy > loose.redundancy
    assert tight.flow_low > loose.flow_low


def test_segments_without_embeddings_are_ignored() -> None:
    segments = corpus(40)
    for seg in segments[:20]:
        seg.embedding = None
    assert Calibration.from_corpus(segments).source == "corpus:20"


def test_context_threshold_uses_the_context_distribution_when_given() -> None:
    segments = corpus(40)
    without = Calibration.from_corpus(segments)
    with_ctx = Calibration.from_corpus(
        segments, {"a prerequisite": list(segments[0].embedding or [])}
    )
    assert with_ctx.context_covered != without.context_covered


# ---- the terms actually use them -----------------------------------------


def ctx_with(calibration: Calibration, segments: list) -> PlanContext:
    return PlanContext(
        query_vec=list(segments[0].embedding or []),
        target_duration=300.0,
        calibration=calibration,
    )


def test_non_repetition_penalises_near_duplicates_once_the_cut_is_realistic(cosine_sim) -> None:
    """Two tellings of the same bit must cost something. Set the cut above
    what the model ever produces and they cost exactly nothing — which is the
    state the fixed 0.82 threshold put this term in after the encoder swap."""
    # cos ~= 0.9988, near-identical but not identical: a cut above it is
    # unreachable, a cut below it bites.
    pair = [make_segment("a", angle=0.0), make_segment("b", angle=0.05)]

    unreachable = Calibration(redundancy=0.9995)
    # Only the topic-monotony half of the term survives.
    assert term_non_repetition(pair, cosine_sim, unreachable) == pytest.approx(0.85, abs=1e-3)

    realistic = Calibration(redundancy=0.80)
    assert term_non_repetition(pair, cosine_sim, realistic) < 0.2


def test_non_repetition_defaults_to_the_fixed_threshold(cosine_sim) -> None:
    pair = [make_segment("a", angle=0.0), make_segment("b", angle=0.05)]
    assert term_non_repetition(pair, cosine_sim) == term_non_repetition(
        pair, cosine_sim, Calibration()
    )


def test_progression_flow_band_comes_from_the_calibration(cosine_sim) -> None:
    """Same sequence, two bands: the score has to move, or the band is
    decorative."""
    seq = [make_segment(f"s{i}", angle=0.25 * i) for i in range(4)]
    narrow = ctx_with(Calibration(flow_low=0.0, flow_high=0.2), seq)
    wide = ctx_with(Calibration(flow_low=0.5, flow_high=0.99), seq)
    assert term_progression(seq, narrow, cosine_sim) != term_progression(seq, wide, cosine_sim)


def test_prepare_context_calibrates_from_the_material_in_play() -> None:
    segments = corpus(40)
    ctx = PlanContext(query_vec=list(segments[0].embedding or []), target_duration=300.0)
    prepare_context(ctx, segments, lambda texts: [[1.0, 0.0] for _ in texts])
    assert ctx.calibration.source == "corpus:40"


def test_calibration_can_be_suppressed() -> None:
    """The editor restores the build's thresholds instead of re-deriving its
    own against a differently-shaped corpus."""
    segments = corpus(40)
    ctx = PlanContext(query_vec=list(segments[0].embedding or []), target_duration=300.0)
    prepare_context(ctx, segments, lambda texts: [], calibrate=False)
    assert ctx.calibration == Calibration()


# ---- persistence ---------------------------------------------------------


def test_calibration_round_trips_through_a_dict() -> None:
    original = Calibration.from_corpus(corpus(40))
    restored = Calibration.from_dict(original.as_dict())
    for field_name in ("redundancy", "context_covered", "flow_low", "flow_high"):
        assert getattr(restored, field_name) == pytest.approx(
            getattr(original, field_name), abs=1e-4
        )


def test_restoring_nothing_yields_the_defaults() -> None:
    assert Calibration.from_dict(None) == Calibration()
    assert Calibration.from_dict({}) == Calibration()


def test_restoring_a_partial_record_keeps_the_defaults_for_the_rest() -> None:
    restored = Calibration.from_dict({"redundancy": 0.7})
    assert restored.redundancy == 0.7
    assert restored.flow_high == Calibration().flow_high


# ---- the regression this exists to prevent -------------------------------


@pytest.mark.parametrize("spread", [0.6, 1.6, 2.8])
def test_a_repeated_bit_is_caught_at_any_similarity_scale(cosine_sim, spread: float) -> None:
    """The regression this whole mechanism exists to prevent.

    `spread` stands in for the encoder: a low value is a model that rates
    everything mutually similar, a high one a model that spreads material out.
    A duplicated bit has to be penalised under all of them; with a fixed
    threshold it is caught under one scale and invisible under the others.

    Both pairs carry distinct topics so the topic-monotony half of the term is
    held constant and only the vector duplication can move the score.
    """
    calibration = Calibration.from_corpus(corpus(40, spread=spread))

    distinct = [
        make_segment("a", angle=0.0, topics=["money"]),
        make_segment("b", angle=spread * 0.9, topics=["marriage"]),
    ]
    duplicated = [
        make_segment("a", angle=0.0, topics=["money"]),
        make_segment("b", angle=0.0, topics=["marriage"]),
    ]

    assert term_non_repetition(duplicated, cosine_sim, calibration) < term_non_repetition(
        distinct, cosine_sim, calibration
    )


@pytest.mark.parametrize("spread", [0.6, 1.6, 2.8])
def test_the_calibrated_cut_always_sits_inside_the_observed_range(spread: float) -> None:
    """A cut above everything makes the term inert; a cut below everything
    makes it fire on every pair. Both are silent failures."""
    segments = corpus(40, spread=spread)
    calibration = Calibration.from_corpus(segments)

    # Use the same float32 path the calibration itself takes: recomputing in
    # float64 shifts the maximum in the last decimal and the comparison stops
    # meaning anything.
    matrix = np.vstack([_unit(s.embedding or []) for s in segments])
    upper = (matrix @ matrix.T)[np.triu_indices(len(matrix), k=1)]

    assert float(upper.min()) < calibration.redundancy < float(upper.max())
    assert float(upper.min()) <= calibration.flow_low < calibration.flow_high
