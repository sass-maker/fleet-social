"""Sequence planning.

All three AI strategies run the *same* beam search over the *same* objective.
They differ only in their weight profile and in the ordering constraint they
impose. That is deliberate: if escalation beats chronological in the blind
test, the difference is attributable to the objective rather than to one
planner having been hand-tuned harder than another.

The two baselines live here too, for the same reason — the comparison is only
meaningful if the strawman is built with the same machinery.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, replace

from mashup.models import ScoreTerms, Segment
from mashup.plan.score import (
    WEIGHT_PROFILES,
    PlanContext,
    profile_for,
    score_sequence,
    total_score,
)
from mashup.retrieve import Candidate

SimFn = Callable[[Segment, Segment], float]

# Stop adding clips once the sequence is within this fraction of the target.
DURATION_TOLERANCE = 0.06
# Never overshoot the target by more than this.
DURATION_CEILING = 1.10
# A set that stops on a clip the model says cannot end feels unfinished.
UNFINISHED_PENALTY = 0.94


def ending_penalty(seq: list[Segment]) -> float:
    """Multiplier for stopping somewhere the model says is not an ending.

    Shared by `plan` and `rescore` rather than living inside the search. It
    used to be applied only when planning, so any sequence scored afterwards —
    a human edit, or the same clips in a different order — silently skipped a
    6% penalty the planner had paid. Two scores that came from different
    functions were then not comparable, which is exactly what the matched-pair
    experiment needs them to be.

    An empty sequence is unpenalised: there is no ending to judge, and its
    score is zero regardless.
    """
    if not seq:
        return 1.0
    return 1.0 if seq[-1].meta.can_end else UNFINISHED_PENALTY


@dataclass
class PlanResult:
    strategy: str
    sequence: list[Segment]
    terms: ScoreTerms
    score: float
    rationale: list[str]


@dataclass(frozen=True)
class _Beam:
    seq: tuple[Segment, ...]
    duration: float
    score: float

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(member for segment in self.seq for member in segment.material_ids)


def _search_weights(strategy: str) -> dict[str, float]:
    """Weights used *during* search.

    duration_fit is dropped while the sequence is still being built — a
    two-clip prefix of a seven-minute set would otherwise be scored as a
    catastrophic duration miss and pruned before it could grow.
    """
    w = dict(WEIGHT_PROFILES[strategy])
    w["duration_fit"] = 0.0
    scale = sum(w.values())
    return {k: v / scale for k, v in w.items()} if scale else w


def _chronological_key(seg: Segment, ordinals: dict[str, int]) -> tuple[int, float]:
    return (ordinals.get(seg.source_id, 0), seg.start)


def plan(
    strategy: str,
    candidates: list[Candidate],
    ctx: PlanContext,
    sim: SimFn,
    *,
    beam_width: int = 6,
    branch: int = 14,
    max_clips: int = 24,
) -> PlanResult:
    """Beam-search a sequence under `strategy`'s objective."""
    if strategy not in WEIGHT_PROFILES:
        raise ValueError(f"unknown strategy: {strategy}")
    if not candidates:
        raise ValueError("no candidates to plan over")

    pool = [c.segment for c in candidates]
    search_w = _search_weights(strategy)
    target = ctx.target_duration
    chronological = strategy == "chronological"

    def partial_score(seq: tuple[Segment, ...]) -> float:
        return score_sequence(list(seq), ctx, sim).total(search_w)

    # Seed: openers only, when the archive offers any.
    openers = [s for s in pool if s.meta.can_open] or pool
    if chronological:
        # Seeding on relevance alone routinely started in the back half of the
        # archive, and the monotonic constraint then starves the search — it
        # can only walk forward through whatever little remains. Observed
        # opening at episode 6 of 20 and running 211s against a 420s target.
        # Starting at the front is also simply what "chronological" means.
        seeds = sorted(openers, key=lambda s: _chronological_key(s, ctx.source_ordinals))[:branch]
    else:
        seeds = sorted(openers, key=lambda s: ctx.relevance_of(s), reverse=True)[:branch]
    beams = [
        _Beam(seq=(s,), duration=s.duration, score=partial_score((s,)))
        for s in seeds
        if s.duration <= target * DURATION_CEILING
    ]
    if not beams:
        best = max(pool, key=ctx.relevance_of)
        beams = [_Beam(seq=(best,), duration=best.duration, score=0.0)]
    beams = sorted(beams, key=lambda b: b.score, reverse=True)[:beam_width]

    finished: list[_Beam] = []

    for _ in range(max_clips - 1):
        nxt: list[_Beam] = []
        for beam in beams:
            remaining = target - beam.duration
            if remaining <= target * DURATION_TOLERANCE:
                finished.append(beam)
                continue

            used = beam.ids
            options = [s for s in pool if not (s.material_ids & used)]
            if chronological:
                last = beam.seq[-1]
                last_key = _chronological_key(last, ctx.source_ordinals)
                options = [
                    s for s in options if _chronological_key(s, ctx.source_ordinals) > last_key
                ]
            options = [
                s for s in options if beam.duration + s.duration <= target * DURATION_CEILING
            ]
            if not options:
                finished.append(beam)
                continue

            # Cheap pre-rank keeps the expensive full scoring to `branch`
            # items. For chronological the pre-rank has to be archive order,
            # not relevance: ranking by relevance offers the beam the best
            # clips anywhere ahead of it, it leaps to them, and the monotonic
            # constraint then discards everything skipped over. Measured, that
            # capped the search at 348s of a 420s target no matter how large
            # the candidate pool grew. Walking to the nearest forward clips
            # keeps the remaining archive spendable; the objective still
            # chooses between them.
            if chronological:
                options.sort(key=lambda s: _chronological_key(s, ctx.source_ordinals))
            else:
                options.sort(key=ctx.relevance_of, reverse=True)
            for seg in options[:branch]:
                seq = (*beam.seq, seg)
                nxt.append(
                    _Beam(
                        seq=seq,
                        duration=beam.duration + seg.duration,
                        score=partial_score(seq),
                    )
                )
        if not nxt:
            break
        beams = sorted(nxt, key=lambda b: b.score, reverse=True)[:beam_width]

    finished.extend(beams)

    scored: list[tuple[float, ScoreTerms, _Beam]] = []
    for beam in finished:
        seq = list(beam.seq)
        terms = score_sequence(seq, ctx, sim)
        s = total_score(terms, strategy) * ending_penalty(seq)
        scored.append((s, terms, beam))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Prefer any sequence that reaches the requested length over a
    # better-scoring short one. `relevance` and `context_completeness` are
    # means over clips, so every extra clip that is merely good rather than
    # excellent drags them down: the objective has a standing bias toward
    # stopping early, and duration_fit's weight is far too small to offset it.
    # Left alone, `chronological` returned 214s against a 420s target and
    # still outscored the full-length variants.
    floor = target * (1 - DURATION_TOLERANCE)
    in_band = [row for row in scored if row[2].duration >= floor]
    best_score, best_terms, best_beam = (in_band or scored)[0]

    seq = list(best_beam.seq)
    rationale = _rationale(strategy, seq, best_terms, ctx)
    if not in_band:
        rationale.append(
            f"ran short: no sequence reached {floor:.0f}s, so the pool is the binding constraint"
        )
    return PlanResult(
        strategy=strategy,
        sequence=seq,
        terms=best_terms,
        score=best_score,
        rationale=rationale,
    )


def _rationale(strategy: str, seq: list[Segment], terms: ScoreTerms, ctx: PlanContext) -> list[str]:
    lines = [
        f"{strategy}: {len(seq)} clips, "
        f"{sum(s.duration for s in seq):.0f}s against a {ctx.target_duration:.0f}s target",
        f"drawn from {len({s.source_id for s in seq})} sources",
    ]
    weak = sorted(
        ((getattr(terms, k), k) for k in WEIGHT_PROFILES[strategy] if WEIGHT_PROFILES[strategy][k]),
    )[:2]
    for value, name in weak:
        lines.append(f"weakest term — {name}: {value:.2f}")
    missing = [s.id for s in seq if s.meta.required_context and not s.meta.can_open]
    if missing:
        lines.append(f"clips carrying prerequisites: {', '.join(missing[:4])}")
    if ctx.beat_labels:
        lines.append("requested structure: " + " -> ".join(ctx.beat_labels))
    return lines


# ---- baselines ----------------------------------------------------------


def plan_semantic(candidates: list[Candidate], ctx: PlanContext, sim: SimFn) -> PlanResult:
    """The strawman: take the most relevant clips, join them in relevance
    order. This is what 'retrieve and concatenate' looks like, and it is the
    bar the AI cuts have to clear."""
    seq: list[Segment] = []
    total = 0.0
    used: set[str] = set()
    for cand in sorted(candidates, key=lambda c: c.relevance, reverse=True):
        if cand.segment.material_ids & used:
            continue
        if total + cand.segment.duration > ctx.target_duration * DURATION_CEILING:
            continue
        seq.append(cand.segment)
        used.update(cand.segment.material_ids)
        total += cand.segment.duration
        if total >= ctx.target_duration * (1 - DURATION_TOLERANCE):
            break
    terms = score_sequence(seq, ctx, sim)
    return PlanResult(
        strategy="semantic",
        sequence=seq,
        terms=terms,
        score=total_score(terms, "semantic"),
        rationale=["baseline: top-relevance clips in relevance order, no sequencing"],
    )


def plan_random(
    candidates: list[Candidate],
    ctx: PlanContext,
    sim: SimFn,
    *,
    seed: int = 0,
    relevance_floor: float = 0.25,
) -> PlanResult:
    """Control condition: topic-matched but unordered."""
    rng = random.Random(seed)
    pool = [c for c in candidates if c.relevance >= relevance_floor] or list(candidates)
    rng.shuffle(pool)
    seq: list[Segment] = []
    total = 0.0
    used: set[str] = set()
    for cand in pool:
        if cand.segment.material_ids & used:
            continue
        if total + cand.segment.duration > ctx.target_duration * DURATION_CEILING:
            continue
        seq.append(cand.segment)
        used.update(cand.segment.material_ids)
        total += cand.segment.duration
        if total >= ctx.target_duration * (1 - DURATION_TOLERANCE):
            break
    terms = score_sequence(seq, ctx, sim)
    return PlanResult(
        strategy="random",
        sequence=seq,
        terms=terms,
        score=total_score(terms, "semantic"),
        rationale=["control: random topic-matched clips in random order"],
    )


def rescore(result: PlanResult, ctx: PlanContext, sim: SimFn) -> PlanResult:
    """Recompute after a human edits the timeline, or after a reorder."""
    terms = score_sequence(result.sequence, ctx, sim)
    strategy = profile_for(result.strategy)
    return replace(
        result,
        terms=terms,
        score=total_score(terms, strategy) * ending_penalty(result.sequence),
    )
