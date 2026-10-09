"""The validation experiment's own bookkeeping.

This module decides whether the project's central claim passes or fails, so
its arithmetic is worth testing more carefully than the code it grades.
"""

from __future__ import annotations

import csv
import json

import pytest

from mashup.experiment import (
    CONDITIONS,
    MATCHED_ARMS,
    MATCHED_VIEWERS,
    VIEWERS,
    Blind,
    _two_sided_binomial,
    summarise_matched,
    summarise_ratings,
    viewing_orders,
    write_rating_sheet,
)

LABELS = ["A", "B", "C", "D", "E"]


def _blinds(tmp_path):
    return [
        Blind(label=label, condition=cond, edl_path=tmp_path / f"{label}.json")
        for label, cond in zip(LABELS, CONDITIONS, strict=True)
    ]


# ---- viewing order ------------------------------------------------------


def test_every_variant_takes_every_position_exactly_once():
    """The Latin square property. Without it, position effects — fresh
    attention first, fatigue last — land on whichever variant is fixed there."""
    orders = viewing_orders(LABELS)
    for position in range(len(LABELS)):
        seen = [order[position] for order in orders]
        assert sorted(seen) == LABELS


def test_each_viewer_sees_all_variants_once():
    for order in viewing_orders(LABELS):
        assert sorted(order) == LABELS


def test_orders_are_distinct():
    orders = viewing_orders(LABELS)
    assert len({tuple(o) for o in orders}) == VIEWERS


def test_order_is_deterministic():
    """Two people generating the sheet must hand raters the same order."""
    assert viewing_orders(LABELS) == viewing_orders(LABELS)


def test_labels_need_not_arrive_sorted():
    assert viewing_orders(["E", "C", "A", "D", "B"]) == viewing_orders(LABELS)


# ---- rating sheet -------------------------------------------------------


def test_sheet_has_one_row_per_viewer_per_variant(tmp_path):
    path = tmp_path / "ratings.csv"
    write_rating_sheet(_blinds(tmp_path), path)
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == VIEWERS * len(LABELS)
    assert {r["variant"] for r in rows} == set(LABELS)


def test_sheet_rows_are_in_viewing_order(tmp_path):
    """The rater works down the sheet, so row order *is* the instruction."""
    path = tmp_path / "ratings.csv"
    write_rating_sheet(_blinds(tmp_path), path)
    rows = list(csv.DictReader(path.open()))
    for viewer, expected in enumerate(viewing_orders(LABELS), start=1):
        got = [r["variant"] for r in rows if r["viewer"] == str(viewer)]
        assert got == expected
        positions = [r["position"] for r in rows if r["viewer"] == str(viewer)]
        assert positions == ["1", "2", "3", "4", "5"]


def test_sheet_leaves_judgement_columns_blank(tmp_path):
    path = tmp_path / "ratings.csv"
    write_rating_sheet(_blinds(tmp_path), path)
    for row in csv.DictReader(path.open()):
        for field in ("overall_rank", "clips_total", "defects", "would_publish", "notes"):
            assert row[field] == ""


# ---- unblinding ---------------------------------------------------------


def _write_key(tmp_path):
    mapping = dict(zip(LABELS, CONDITIONS, strict=True))
    (tmp_path / "KEY.json").write_text(json.dumps({"mapping": mapping}))
    return {cond: label for label, cond in mapping.items()}


def _fill(tmp_path, ranks_by_viewer, **extra):
    """Write a completed sheet. `ranks_by_viewer` maps condition -> rank."""
    label_of = _write_key(tmp_path)
    path = tmp_path / "ratings.csv"
    write_rating_sheet(_blinds(tmp_path), path)
    rows = list(csv.DictReader(path.open()))
    for row in rows:
        viewer = int(row["viewer"])
        if viewer > len(ranks_by_viewer):
            continue
        ranks = ranks_by_viewer[viewer - 1]
        for cond, rank in ranks.items():
            if row["variant"] == label_of[cond]:
                row["overall_rank"] = str(rank)
                row.update(extra)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_added_position_column_does_not_break_unblinding(tmp_path):
    """The sheet grew a column; the analysis reads by name, not by index."""
    _fill(tmp_path, [{"escalation": 1, "semantic": 2}] * 5)
    result = summarise_ratings(tmp_path)
    assert result["viewers"] == 5
    assert result["beats_semantic"]["escalation"] == 5


def test_preference_criterion_needs_four_of_five(tmp_path):
    ranks = [{"escalation": 1, "semantic": 2}] * 3 + [{"escalation": 2, "semantic": 1}] * 2
    _fill(tmp_path, ranks)
    result = summarise_ratings(tmp_path)
    assert result["beats_semantic"]["escalation"] == 3
    assert result["criteria"]["preference"] is False


def test_fewer_viewers_are_scored_against_the_viewers_present(tmp_path):
    """Two viewers agreeing is not a pass of "four of five", but the harness
    still reports against what it has rather than silently failing."""
    _fill(tmp_path, [{"escalation": 1, "semantic": 2}] * 2)
    result = summarise_ratings(tmp_path)
    assert result["viewers"] == 2
    assert result["criteria"]["preference"] is True


def test_empty_sheet_is_an_error_not_a_verdict(tmp_path):
    _write_key(tmp_path)
    write_rating_sheet(_blinds(tmp_path), tmp_path / "ratings.csv")
    with pytest.raises(RuntimeError, match="no completed rows"):
        summarise_ratings(tmp_path)


def test_context_completeness_is_a_ratio_of_counted_clips(tmp_path):
    _fill(
        tmp_path,
        [{"escalation": 1, "semantic": 2}] * 5,
        clips_total="10",
        clips_context_incomplete="3",
    )
    result = summarise_ratings(tmp_path)
    assert result["context_completeness"]["escalation"] == pytest.approx(0.7)
    assert result["criteria"]["context_complete"] is False


# ---- the matched pair ---------------------------------------------------
#
# The five-condition design compares variants built from different clips —
# measured on the dev archive, the chronological cut shared 0-5% of its
# material with the other four. It therefore cannot attribute a preference to
# sequencing, which is the claim the project actually makes. The matched pair
# holds the clips fixed and varies only their order.


MATCHED_LABELS = ["A", "B"]


def _matched_blinds(tmp_path):
    return [
        Blind(label=label, condition=cond, edl_path=tmp_path / f"{label}.json")
        for label, cond in zip(MATCHED_LABELS, MATCHED_ARMS, strict=True)
    ]


def _fill_matched(tmp_path, winners):
    """`winners` is one entry per viewer: the arm that viewer ranked first,
    or None for a viewer who called them equal."""
    mapping = dict(zip(MATCHED_LABELS, MATCHED_ARMS, strict=True))
    (tmp_path / "KEY.json").write_text(json.dumps({"design": "matched", "mapping": mapping}))
    label_of = {cond: label for label, cond in mapping.items()}

    path = tmp_path / "ratings.csv"
    write_rating_sheet(_matched_blinds(tmp_path), path, viewers=len(winners))
    rows = list(csv.DictReader(path.open()))
    for row in rows:
        winner = winners[int(row["viewer"]) - 1]
        if winner is None:
            row["overall_rank"] = "1"
        else:
            row["overall_rank"] = "1" if row["variant"] == label_of[winner] else "2"
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_matched_sheet_alternates_which_arm_plays_first(tmp_path):
    """With two arms the square degenerates to alternation, which is exactly
    what balances the order effect across an even number of viewers."""
    path = tmp_path / "ratings.csv"
    write_rating_sheet(_matched_blinds(tmp_path), path, viewers=MATCHED_VIEWERS)
    first = [r["variant"] for r in csv.DictReader(path.open()) if r["position"] == "1"]
    assert first.count("A") == first.count("B") == MATCHED_VIEWERS // 2


def test_unanimous_preference_for_the_planner_is_significant(tmp_path):
    _fill_matched(tmp_path, ["planned"] * 6)
    result = summarise_matched(tmp_path)
    assert result["planned_preferred"] == 6
    assert result["significant"]


def test_five_of_six_is_reported_but_not_called_significant(tmp_path):
    """Six viewers cannot reach p < 0.05 at 5-1. Rounding that up to a result
    would defeat the point of running the experiment at all."""
    _fill_matched(tmp_path, ["planned"] * 5 + ["shuffled"])
    result = summarise_matched(tmp_path)
    assert result["planned_preferred"] == 5
    assert not result["significant"]
    assert "5 of 6" in result["verdict"]


def test_a_split_verdict_is_not_significant(tmp_path):
    _fill_matched(tmp_path, ["planned"] * 3 + ["shuffled"] * 3)
    result = summarise_matched(tmp_path)
    assert result["planned_preferred"] == 3
    assert result["p_value"] == 1.0
    assert not result["significant"]


def test_unanimous_preference_against_the_planner_is_also_significant(tmp_path):
    """The test is two-sided: shuffled winning 6-0 is a real finding, not a
    null one, and must not be reported as 'no effect'."""
    _fill_matched(tmp_path, ["shuffled"] * 6)
    result = summarise_matched(tmp_path)
    assert result["planned_preferred"] == 0
    assert result["significant"]


def test_viewers_who_tied_the_arms_are_excluded_from_the_count(tmp_path):
    _fill_matched(tmp_path, ["planned"] * 4 + [None, None])
    result = summarise_matched(tmp_path)
    assert result["viewers"] == 6
    assert result["decided"] == 4
    assert result["planned_preferred"] == 4


def test_matched_sheet_with_no_completed_rows_raises(tmp_path):
    mapping = dict(zip(MATCHED_LABELS, MATCHED_ARMS, strict=True))
    (tmp_path / "KEY.json").write_text(json.dumps({"design": "matched", "mapping": mapping}))
    write_rating_sheet(_matched_blinds(tmp_path), tmp_path / "ratings.csv", viewers=6)
    with pytest.raises(RuntimeError, match="no completed rows"):
        summarise_matched(tmp_path)


# ---- the sign test ------------------------------------------------------


@pytest.mark.parametrize(
    ("wins", "n", "expected"),
    [
        (6, 6, 2 / 64),  # both tails of a 6-flip run
        (0, 6, 2 / 64),
        (5, 6, 14 / 64),
        (3, 6, 1.0),
        (0, 0, 1.0),
    ],
)
def test_two_sided_binomial_matches_the_exact_distribution(wins, n, expected):
    assert _two_sided_binomial(wins, n) == pytest.approx(expected)


def test_binomial_never_exceeds_one():
    for n in range(1, 12):
        for wins in range(n + 1):
            assert 0.0 < _two_sided_binomial(wins, n) <= 1.0
