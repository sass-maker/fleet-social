"""Whether the archive holds material on a topic at all.

This gate exists because of a measured near-miss: a five-condition study was
generated, rendered and prepared for viewers on the brief "seven minutes on
airline travel" against an archive containing three segments on the subject.
Every variant looked plausible. Nothing in the pipeline objected, because
cosine similarity from an asymmetric encoder never returns anything near
zero — nonsense scored 0.43 against the same corpus.
"""

from __future__ import annotations

import math

import pytest
from conftest import make_segment

from mashup.retrieve import MIN_LIFT, Coverage, Retriever, nonsense_probes


def _vec(angle: float, dim: int = 8) -> list[float]:
    v = [0.0] * dim
    v[0], v[1] = math.cos(angle), math.sin(angle)
    return v


def _archive() -> Retriever:
    """Twenty segments fanned around angle 0, so a query at 0 matches well
    and a query at right angles matches nothing."""
    return Retriever([make_segment(f"s{i:02d}", angle=0.01 * i) for i in range(20)])


# ---- the nonsense probes ------------------------------------------------


def test_probes_are_deterministic():
    """The floor has to be reproducible or two runs of `coverage` on the same
    archive disagree about whether a brief is viable."""
    assert nonsense_probes() == nonsense_probes()


def test_probes_differ_from_each_other():
    assert len(set(nonsense_probes())) == len(nonsense_probes())


def test_probe_count_is_honoured():
    assert len(nonsense_probes(5)) == 5


# ---- the measure --------------------------------------------------------


def test_query_matching_the_corpus_is_viable():
    r = _archive()
    cov = r.coverage(_vec(0.0), [_vec(math.pi / 2), _vec(-math.pi / 2)])
    assert cov.lift > MIN_LIFT
    assert cov.viable


def test_query_no_better_than_the_probes_is_not_viable():
    """A query indistinguishable from the floor must fail even though its raw
    similarity is high — that is the whole point of measuring the floor."""
    r = _archive()
    orthogonal = _vec(math.pi / 2)
    cov = r.coverage(orthogonal, [orthogonal, orthogonal])
    assert cov.lift == pytest.approx(0.0, abs=1e-6)
    assert not cov.viable


def test_supporting_counts_only_segments_above_the_luckiest_probe():
    r = _archive()
    cov = r.coverage(_vec(0.0), [_vec(math.pi / 2)])
    assert 0 < cov.supporting <= len(r.segments)


def test_coverage_needs_probes():
    with pytest.raises(ValueError, match="nonsense probes"):
        _archive().coverage(_vec(0.0), [])


def test_top_k_clamps_to_a_small_archive():
    r = Retriever([make_segment("a", angle=0.0), make_segment("b", angle=0.01)])
    cov = r.coverage(_vec(0.0), [_vec(math.pi / 2)], top_k=10)
    assert cov.top_k == 2


# ---- reporting ----------------------------------------------------------


def test_explain_names_the_consequence_when_unviable():
    cov = Coverage(top_k_mean=0.44, floor=0.43, ceiling=0.47, supporting=1, top_k=10)
    assert not cov.viable
    assert "near-arbitrary" in cov.explain()


def test_explain_is_positive_when_viable():
    cov = Coverage(top_k_mean=0.60, floor=0.43, ceiling=0.47, supporting=40, top_k=10)
    assert cov.viable
    assert "40 segments" in cov.explain()


def test_as_dict_round_trips_the_verdict():
    cov = Coverage(top_k_mean=0.60, floor=0.43, ceiling=0.47, supporting=40, top_k=10)
    assert cov.as_dict()["viable"] is True
    assert cov.as_dict()["lift"] == pytest.approx(0.17, abs=1e-4)
