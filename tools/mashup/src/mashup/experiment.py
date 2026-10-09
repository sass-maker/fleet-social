"""The validation experiment.

The project exists to answer one question: does structure-aware sequencing
beat retrieving relevant clips and joining them? That question is only
answerable if the comparison is blind and the churn measurement is mechanical,
so both live in code rather than in a spreadsheet someone fills in by hand.

Five conditions, per the PRD:
  random          topic-matched, unordered      (control)
  semantic        relevance-sorted              (the bar to beat)
  chronological   AI cut
  escalation      AI cut
  callback        AI cut
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mashup.config import Config
from mashup.models import EDL
from mashup.pipeline import DEFAULT_POOL, MATCHED_ARMS, make_mashups, make_matched_pair
from mashup.render import save_edl

CONDITIONS = ("random", "semantic", "chronological", "escalation", "callback")
AI_CONDITIONS = ("chronological", "escalation", "callback")

# From the PRD's success criteria.
PREFERENCE_THRESHOLD = 4  # of 5 viewers
CONTEXT_COMPLETE_TARGET = 0.80
MAX_DEFECTS_PER_SEVEN_MIN = 2
MAX_CHURN = 0.30  # kill criterion: creators replacing more than this


@dataclass
class Blind:
    label: str  # A..E, shown to viewers
    condition: str  # the real strategy, hidden until unblinding
    edl_path: Path


def run_experiment(
    prompt: str,
    cfg: Config,
    *,
    outdir: Path,
    target: float,
    seed: int = 0,
    snap: bool = True,
    pool: int = DEFAULT_POOL,
) -> list[Blind]:
    """Generate all five conditions under blind labels.

    `pool` matters more than it looks. A pool too small for the archive starves
    the chronological strategy in particular — it can only move forward through
    archive order, so it runs out of valid continuations and returns a short
    sequence. Unequal runtimes across the five variants leak the blinding, so
    the pool is recorded in KEY.json alongside the mapping.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    edls = make_mashups(
        prompt,
        cfg,
        target=target,
        strategies=AI_CONDITIONS,
        include_baselines=True,
        snap=snap,
        pool=pool,
    )
    by_condition = {e.strategy: e for e in edls}
    missing = [c for c in CONDITIONS if c not in by_condition]
    if missing:
        raise RuntimeError(f"planner produced no output for: {', '.join(missing)}")

    order = list(CONDITIONS)
    random.Random(seed).shuffle(order)
    labels = [chr(ord("A") + i) for i in range(len(order))]

    blinds: list[Blind] = []
    for label, condition in zip(labels, order, strict=True):
        path = outdir / f"{label}.json"
        save_edl(by_condition[condition], path)
        blinds.append(Blind(label=label, condition=condition, edl_path=path))

    # The key is written separately so it can be withheld from raters.
    (outdir / "KEY.json").write_text(
        json.dumps(
            {
                "prompt": prompt,
                "target_duration": target,
                "seed": seed,
                "pool": pool,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "mapping": {b.label: b.condition for b in blinds},
                "scores": {b.label: by_condition[b.condition].score for b in blinds},
            },
            indent=2,
        )
    )
    write_rating_sheet(blinds, outdir / "ratings.csv")
    return blinds


# Two arms, six viewers: the clip set is identical, so the only thing a
# ranking can express is a preference about order. Six rather than five
# because an even count splits which arm plays first exactly in half.
MATCHED_VIEWERS = 6


def run_matched_experiment(
    prompt: str,
    cfg: Config,
    *,
    outdir: Path,
    target: float,
    strategy: str = "escalation",
    seed: int = 0,
    snap: bool = True,
    pool: int = DEFAULT_POOL,
) -> list[Blind]:
    """Blind A/B on ordering alone: identical clips, planner order vs shuffled.

    The five-condition experiment cannot attribute a preference to sequencing,
    because its conditions are built from different clips — on the dev archive
    the chronological cut shared 0-5% of its material with the other four. It
    measures the pipeline end to end, which is worth knowing but is not the
    claim the project makes.

    This is the claim: given the same material, does the planner's order beat
    an arbitrary one? Two arms, so a viewer's answer is a forced choice.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    pair = make_matched_pair(
        prompt, cfg, target=target, strategy=strategy, pool=pool, seed=seed, snap=snap
    )

    order = list(MATCHED_ARMS)
    random.Random(seed).shuffle(order)
    blinds: list[Blind] = []
    for label, arm in zip(("A", "B"), order, strict=True):
        path = outdir / f"{label}.json"
        save_edl(pair[arm], path)
        blinds.append(Blind(label=label, condition=arm, edl_path=path))

    (outdir / "KEY.json").write_text(
        json.dumps(
            {
                "design": "matched",
                "prompt": prompt,
                "target_duration": target,
                "strategy": strategy,
                "seed": seed,
                "pool": pool,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "mapping": {b.label: b.condition for b in blinds},
                "scores": {b.label: pair[b.condition].score for b in blinds},
                # Where the planner's order sits among arbitrary ones. If this
                # is near 50, the objective itself says the planner did not
                # order these clips well, and a null result from the viewers
                # would be confirming that rather than refuting the thesis.
                "order_stats": pair.stats,
                "clips": [c.segment_id for c in pair.planned.clips],
            },
            indent=2,
        )
    )
    write_rating_sheet(blinds, outdir / "ratings.csv", viewers=MATCHED_VIEWERS)
    return blinds


def summarise_matched(outdir: Path) -> dict:
    """Sign test on the matched pair: did the planner's order win?

    Reports the exact two-sided binomial p-value rather than a pass/fail
    against a threshold, because with six viewers only unanimity reaches
    p < 0.05. A 5-1 split is worth acting on as evidence but is not a result,
    and rounding it up to one would be the whole point of this experiment
    thrown away.
    """
    mapping = _load_key(outdir)
    rows = [
        r
        for r in csv.DictReader((outdir / "ratings.csv").open())
        if (r.get("overall_rank") or "").strip()
    ]
    if not rows:
        raise RuntimeError("ratings.csv has no completed rows")

    by_viewer: dict[str, dict[str, int]] = {}
    for row in rows:
        by_viewer.setdefault(row["viewer"], {})[mapping[row["variant"]]] = int(row["overall_rank"])

    decided = [
        r
        for r in by_viewer.values()
        if "planned" in r and "shuffled" in r and r["planned"] != r["shuffled"]
    ]
    wins = sum(1 for r in decided if r["planned"] < r["shuffled"])
    n = len(decided)
    p = _two_sided_binomial(wins, n) if n else 1.0
    return {
        "viewers": len(by_viewer),
        "decided": n,
        "planned_preferred": wins,
        "p_value": round(p, 4),
        "significant": p < 0.05,
        "verdict": (
            f"planner order preferred by {wins} of {n} viewers (p={p:.3f})"
            if n
            else "no viewer expressed a preference"
        ),
    }


def _two_sided_binomial(wins: int, n: int) -> float:
    """P(a fair coin is at least this lopsided), no scipy needed."""
    if n == 0:
        return 1.0
    extreme = max(wins, n - wins)
    tail = sum(math.comb(n, k) for k in range(extreme, n + 1)) / 2**n
    return min(1.0, 2 * tail)


VIEWERS = 5


def viewing_orders(labels: list[str], viewers: int = VIEWERS) -> list[list[str]]:
    """One viewing order per viewer, rotated so no variant keeps a position.

    Showing every viewer A..E in label order confounds the variant with when it
    was watched: whatever plays first is judged fresh and sets the anchor for
    the rest, and whatever plays last is judged tired. Those effects would land
    entirely on one condition. A cyclic square gives each variant each position
    exactly once across five viewers, so position averages out of the ranking.

    It balances position, not carryover — variant order pairs are not balanced,
    which would need ten viewers for five variants. With five viewers this is
    the most that can be balanced.
    """
    ordered = sorted(labels)
    return [[ordered[(i + v) % len(ordered)] for i in range(len(ordered))] for v in range(viewers)]


def write_rating_sheet(blinds: list[Blind], path: Path, *, viewers: int = VIEWERS) -> None:
    """One row per viewer per variant, in the order that viewer watches them."""
    orders = viewing_orders([b.label for b in blinds], viewers)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "viewer",
                "position",  # watch in this order; 1 is first
                "variant",
                "overall_rank",  # 1 = best of the five
                "clips_total",
                "clips_context_incomplete",  # count that felt like they needed setup
                "defects",  # obvious repetitions or broken transitions
                "would_publish",  # yes/no
                "notes",
            ]
        )
        for viewer, order in enumerate(orders, start=1):
            for position, label in enumerate(order, start=1):
                writer.writerow([viewer, position, label, "", "", "", "", "", ""])


# ---- analysis -----------------------------------------------------------


def _load_key(outdir: Path) -> dict[str, str]:
    key = json.loads((outdir / "KEY.json").read_text())
    return key["mapping"]


def summarise_ratings(outdir: Path) -> dict:
    """Unblind the ratings and check them against the PRD's criteria."""
    mapping = _load_key(outdir)
    rows = [
        r
        for r in csv.DictReader((outdir / "ratings.csv").open())
        if (r.get("overall_rank") or "").strip()
    ]
    if not rows:
        raise RuntimeError("ratings.csv has no completed rows")

    by_viewer: dict[str, dict[str, int]] = {}
    context_ratio: dict[str, list[float]] = {}
    defects: dict[str, list[float]] = {}

    for row in rows:
        condition = mapping[row["variant"]]
        by_viewer.setdefault(row["viewer"], {})[condition] = int(row["overall_rank"])
        total = float(row.get("clips_total") or 0)
        if total:
            incomplete = float(row.get("clips_context_incomplete") or 0)
            context_ratio.setdefault(condition, []).append(1.0 - incomplete / total)
        if (row.get("defects") or "").strip():
            defects.setdefault(condition, []).append(float(row["defects"]))

    # Criterion 1: an AI cut beats the semantic baseline for >= 4 of 5 viewers.
    beats_semantic = {c: 0 for c in AI_CONDITIONS}
    for ranks in by_viewer.values():
        baseline = ranks.get("semantic")
        if baseline is None:
            continue
        for cond in AI_CONDITIONS:
            if cond in ranks and ranks[cond] < baseline:
                beats_semantic[cond] += 1

    viewers = len(by_viewer)
    best_ai = max(beats_semantic, key=lambda c: beats_semantic[c])

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "viewers": viewers,
        "beats_semantic": beats_semantic,
        "best_ai_condition": best_ai,
        "context_completeness": {c: mean(v) for c, v in context_ratio.items()},
        "defects_mean": {c: mean(v) for c, v in defects.items()},
        "criteria": {
            "preference": beats_semantic[best_ai] >= min(PREFERENCE_THRESHOLD, viewers),
            "context_complete": mean(context_ratio.get(best_ai, [])) >= CONTEXT_COMPLETE_TARGET,
            "defects": mean(defects.get(best_ai, [])) < MAX_DEFECTS_PER_SEVEN_MIN,
        },
    }


def timeline_churn(original: EDL, edited: EDL) -> dict:
    """How much of the generated timeline the creator had to change.

    This is the kill criterion, measured rather than estimated: above 30%
    replacement across three archives, the sequencing is not earning its keep.
    """
    orig_ids = [c.segment_id for c in original.clips]
    edit_ids = [c.segment_id for c in edited.clips]
    orig_set, edit_set = set(orig_ids), set(edit_ids)

    kept = orig_set & edit_set
    removed = orig_set - edit_set
    added = edit_set - orig_set

    # Reorder counts only among survivors, so a removal is not double-charged.
    survivors_orig = [i for i in orig_ids if i in kept]
    survivors_edit = [i for i in edit_ids if i in kept]
    reordered = sum(1 for a, b in zip(survivors_orig, survivors_edit, strict=False) if a != b)

    total = len(orig_ids) or 1
    churn = (len(removed) + len(added)) / (total + len(added))
    return {
        "clips_original": len(orig_ids),
        "clips_edited": len(edit_ids),
        "kept": len(kept),
        "removed": len(removed),
        "added": len(added),
        "reordered": reordered,
        "churn": round(churn, 4),
        "passes_kill_criterion": churn <= MAX_CHURN,
    }
