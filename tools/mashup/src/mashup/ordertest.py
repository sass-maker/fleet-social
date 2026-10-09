"""Does the planner's order beat an arbitrary one? Measured, not assumed.

The blind study is the real test of the project's claim, and it costs human
hours. This is the mechanical proxy you run first: hold a clip set fixed,
shuffle it many times, and see where the planner's own order lands in that
distribution.

It answers three questions the study cannot answer for you:

  Is the planner ahead at all? If its order sits near the median of random
  orders of its own clips, viewers have nothing to prefer and a null result
  would be the planner's fault rather than the thesis's.

  Which configuration should the study use? Percentile moves a lot with
  strategy and pool. Choosing on the objective before any human has rated
  anything is pre-registration, not p-hacking — the objective is the stated
  hypothesis and the outcome has not been observed.

  Is an existing blind set worth rating? Variants built from different clips
  cannot attribute a preference to ordering, and terms that read the same for
  every condition are not contributing to the comparison.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from mashup.config import Config
from mashup.models import EDL, ScoreTerms, Segment
from mashup.pipeline import DEFAULT_POOL, planning_session
from mashup.plan.planner import PlanResult, ending_penalty, plan, rescore
from mashup.plan.score import WEIGHT_PROFILES, PlanContext, profile_for

SimFn = object  # a Callable[[Segment, Segment], float]; kept loose for the type checker

DEFAULT_SHUFFLES = 400

# Below this percentile the planner is not meaningfully ahead of chance on its
# own objective, and a study built on it would be testing the planner rather
# than the hypothesis. Not a significance threshold — a judgement about when a
# configuration is worth six people's time.
CONFIDENT_PERCENTILE = 75.0

# Terms whose value cannot change when a sequence is reordered. Useful to state
# explicitly: it bounds how much of an objective the planner can influence.
ORDER_INVARIANT = ("relevance", "non_repetition", "duration_fit", "source_diversity")
ORDER_SENSITIVE = ("context_completeness", "progression", "escalation", "callback")

# A term varying less than this across conditions is not discriminating between
# them, whatever weight it carries.
DEAD_TERM_SPREAD = 0.10

# Below this, two variants are comparing different material rather than the
# same material in different orders, and a preference between them cannot be
# attributed to sequencing.
SHARED_MATERIAL_FLOOR = 0.50


@dataclass
class Null:
    """Where one order sits among arbitrary orders of the same clips."""

    strategy: str
    clips: int
    actual: float
    scores: list[float] = field(repr=False)

    @property
    def percentile(self) -> float:
        arr = np.asarray(self.scores)
        return float((arr < self.actual).mean() * 100) if len(arr) else 0.0

    @property
    def median(self) -> float:
        return float(np.median(self.scores)) if self.scores else 0.0

    @property
    def best(self) -> float:
        return max(self.scores, default=0.0)

    @property
    def worst(self) -> float:
        return min(self.scores, default=0.0)

    @property
    def gap(self) -> float:
        """How far ahead of a typical arbitrary order this one is."""
        return self.actual - self.median

    @property
    def confident(self) -> bool:
        return self.percentile >= CONFIDENT_PERCENTILE

    def order_blind_weight(self) -> float:
        w = WEIGHT_PROFILES[profile_for(self.strategy)]
        return sum(w[k] for k in ORDER_INVARIANT)

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "clips": self.clips,
            "actual": round(self.actual, 4),
            "median": round(self.median, 4),
            "best": round(self.best, 4),
            "worst": round(self.worst, 4),
            "percentile": round(self.percentile, 1),
            "shuffles": len(self.scores),
            "order_blind_weight": round(self.order_blind_weight(), 2),
        }


def null_distribution(
    sequence: list[Segment],
    ctx: PlanContext,
    sim,
    *,
    strategy: str,
    actual: float,
    shuffles: int = DEFAULT_SHUFFLES,
    seed: int = 0,
) -> Null:
    """Score `shuffles` random orders of the same clips under one profile.

    Scores carry the same ending penalty `plan` applies, so `actual` and the
    distribution are on one scale. Without that they are not comparable, and
    the 6% penalty is large next to the differences ordering produces.
    """
    weights = WEIGHT_PROFILES[profile_for(strategy)]
    template = PlanResult(
        strategy=strategy, sequence=list(sequence), terms=ScoreTerms(), score=0.0, rationale=[]
    )
    rng = random.Random(seed)
    scores: list[float] = []
    for _ in range(shuffles):
        seq = list(sequence)
        rng.shuffle(seq)
        terms = rescore(replace(template, sequence=seq), ctx, sim).terms
        scores.append(terms.total(weights) * ending_penalty(seq))
    return Null(strategy=strategy, clips=len(sequence), actual=actual, scores=scores)


def test_configuration(
    prompt: str,
    cfg: Config,
    *,
    target: float,
    strategy: str = "escalation",
    pool: int = DEFAULT_POOL,
    shuffles: int = DEFAULT_SHUFFLES,
    seed: int = 0,
) -> Null:
    """Plan one configuration and place its order in the null distribution."""
    with planning_session(prompt, cfg, target=target, pool=pool) as session:
        result = plan(strategy, session.pool_for(strategy), session.ctx, session.retriever.pairwise)
        return null_distribution(
            result.sequence,
            session.ctx,
            session.retriever.pairwise,
            strategy=strategy,
            actual=result.score,
            shuffles=shuffles,
            seed=seed,
        )


def sweep(
    prompt: str,
    cfg: Config,
    *,
    target: float,
    strategies: tuple[str, ...],
    pools: tuple[int, ...],
    shuffles: int = DEFAULT_SHUFFLES,
    seed: int = 0,
    progress=None,
) -> list[tuple[int, Null]]:
    """Every strategy at every pool, so the study arm can be chosen on evidence.

    One planning session per pool rather than per cell: retrieval and
    calibration depend only on the pool, and repeating them per strategy was
    the slow part of doing this by hand.
    """
    out: list[tuple[int, Null]] = []
    for pool in pools:
        with planning_session(prompt, cfg, target=target, pool=pool) as session:
            for strategy in strategies:
                result = plan(
                    strategy, session.pool_for(strategy), session.ctx, session.retriever.pairwise
                )
                null = null_distribution(
                    result.sequence,
                    session.ctx,
                    session.retriever.pairwise,
                    strategy=strategy,
                    actual=result.score,
                    shuffles=shuffles,
                    seed=seed,
                )
                out.append((pool, null))
                if progress:
                    progress(pool, strategy, null)
    return out


def best_of(results: list[tuple[int, Null]]) -> tuple[int, Null] | None:
    """The configuration whose order is furthest ahead of chance.

    Ranked on percentile first and the gap second: a percentile of 100 earned
    by a hair is less convincing than one earned by a wide margin, but a
    configuration that loses to a third of arbitrary orders is not rescued by
    a large spread.
    """
    return max(results, key=lambda r: (r[1].percentile, r[1].gap), default=None)


# ---- auditing a blind set that already exists ---------------------------


@dataclass
class StudyAudit:
    prompt: str
    variants: dict[str, EDL]  # condition -> EDL
    overlap: dict[tuple[str, str], float]
    nulls: dict[str, Null]

    def dead_terms(self) -> list[tuple[str, float]]:
        """Terms that read near-identically for every condition."""
        out = []
        for term in ORDER_INVARIANT + ORDER_SENSITIVE:
            vals = [getattr(e.terms, term) for e in self.variants.values()]
            spread = max(vals) - min(vals)
            if spread < DEAD_TERM_SPREAD:
                out.append((term, spread))
        return out

    def constant_share(self, condition: str) -> float:
        """Fraction of a condition's score contributed by near-constant terms."""
        edl = self.variants[condition]
        weights = WEIGHT_PROFILES[profile_for(edl.strategy)]
        total = edl.terms.total(weights)
        if not total:
            return 0.0
        dead = sum(weights[t] * getattr(edl.terms, t) for t, _ in self.dead_terms())
        return dead / total

    def isolated(self, threshold: float = SHARED_MATERIAL_FLOOR) -> list[tuple[str, float]]:
        """Conditions that share almost nothing with any other condition.

        The maximum overlap across the whole set is the wrong summary: on the
        dev archive `callback` and `escalation` shared 0.54, which would have
        suppressed a warning while `chronological` sat at 0.00-0.05 against
        all four of the others. What matters is whether a *particular* variant
        is comparing different material, so this reports each condition's best
        overlap with anything else and names the ones that are on their own.
        """
        out = []
        for cond in self.variants:
            best = max(
                (v for (a, b), v in self.overlap.items() if cond in (a, b)),
                default=0.0,
            )
            if best < threshold:
                out.append((cond, best))
        return sorted(out, key=lambda kv: kv[1])

    def is_matched(self) -> bool:
        """Every variant drawn from one clip set — the matched-pair design."""
        return bool(self.overlap) and all(v >= 1.0 for v in self.overlap.values())

    def order_blind(self) -> list[str]:
        """Conditions whose objective cannot see order at all.

        Their null spread is not evidence about ordering — with every term
        order-invariant, the only thing a shuffle can change is whether the
        sequence happens to end on a `can_end` clip, so the percentile is
        reporting the ending penalty and nothing else.
        """
        return [c for c, n in self.nulls.items() if n.order_blind_weight() >= 1.0]


def audit_study(
    outdir: Path, cfg: Config, *, shuffles: int = DEFAULT_SHUFFLES, seed: int = 0
) -> StudyAudit:
    """Is an existing blind set capable of answering the question?

    Reads the prompt and pool from `KEY.json` but never its `mapping` — each
    variant names its own strategy. The report still reveals which condition
    is which, so it is an operator tool and must not be run in front of a
    rater.
    """
    key = json.loads((outdir / "KEY.json").read_text())
    prompt = key["prompt"]
    target = key["target_duration"]
    pool = key.get("pool", DEFAULT_POOL)

    loaded = [
        (path.stem, EDL.model_validate_json(path.read_text()))
        for path in sorted(outdir.glob("[A-Z].json"))
    ]
    if not loaded:
        raise RuntimeError(f"no blind variants found in {outdir}")

    # Name each variant by its strategy where that is unique — a five-condition
    # set reads far better that way. A matched pair has the same strategy on
    # both arms, so fall back to the blind label rather than let one silently
    # overwrite the other in the dict.
    strategies = [e.strategy for _, e in loaded]
    edls: dict[str, EDL] = {
        (e.strategy if strategies.count(e.strategy) == 1 else f"{label}:{e.strategy}"): e
        for label, e in loaded
    }

    ids = {cond: {c.segment_id for c in e.clips} for cond, e in edls.items()}
    overlap = {
        (a, b): len(ids[a] & ids[b]) / len(ids[a] | ids[b]) for a in ids for b in ids if a < b
    }

    with planning_session(prompt, cfg, target=target, pool=pool) as session:
        by_id = {s.id: s for s in session.retriever.segments}
        nulls = {}
        for cond, edl in edls.items():
            seq = [by_id[c.segment_id] for c in edl.clips if c.segment_id in by_id]
            if len(seq) != len(edl.clips):
                raise RuntimeError(
                    f"{cond}: {len(edl.clips) - len(seq)} clips are no longer in the store, "
                    "so this set cannot be re-scored against the current archive"
                )
            nulls[cond] = null_distribution(
                seq,
                session.ctx,
                session.retriever.pairwise,
                strategy=edl.strategy,
                actual=edl.score,
                shuffles=shuffles,
                seed=seed,
            )

    return StudyAudit(prompt=prompt, variants=edls, overlap=overlap, nulls=nulls)
