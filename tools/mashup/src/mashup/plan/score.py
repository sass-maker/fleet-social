"""Sequence scoring.

The product's claim is that *ordering* material well beats retrieving relevant
material and joining it. That claim only means something if the objective is
written down explicitly, so every term the PRD asks for lives here as a
separate 0..1 signal, and the weights that combine them are what distinguish
one planning strategy from another.

Keeping the terms separate also makes the output inspectable: an EDL carries
its own term breakdown, so a bad mashup can be diagnosed rather than guessed at.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from mashup.models import Role, ScoreTerms, Segment

EmbedFn = Callable[[list[str]], list[list[float]]]

# Fallback thresholds, used only when there is too little material to measure
# the corpus's own distribution. See `Calibration` for why a fixed cosine is
# not a portable way to say "too similar".
REDUNDANCY_THRESHOLD = 0.82
CONTEXT_COVERED_THRESHOLD = 0.55
FLOW_BAND = (0.30, 0.72)

# Percentiles the calibrated thresholds are drawn from.
REDUNDANCY_PCT = 99.0  # "more alike than all but the closest pairs here"
FLOW_PCT = (25.0, 90.0)  # related, but short of interchangeable
CONTEXT_PCT = 25.0  # a weak-but-real match to the prerequisite
# Below this many embedded segments the percentiles are noise, so keep the
# fixed fallbacks. Small unit-test corpora land here deliberately.
MIN_CALIBRATION_SEGMENTS = 12


@dataclass(frozen=True)
class Calibration:
    """Similarity thresholds expressed in the corpus's own distribution.

    Every cosine constant above is a claim about one embedding model's
    similarity scale, not about comedy. Measured on this archive, bge-base
    puts 99.9% of segment pairs below 0.841, so a fixed 0.82 redundancy cut
    fires on almost nothing: `non_repetition` returns 1.00 for every candidate
    sequence and quietly stops discriminating, while looking perfectly
    healthy in the term breakdown. The same swap pushed most adjacent pairs
    inside the fixed flow band, flattening `progression` too.

    Deriving each cut from a percentile of the actual similarity distribution
    makes the terms mean the same thing whichever encoder produced the
    vectors, which is what allows the embedding model to be changed at all.
    """

    redundancy: float = REDUNDANCY_THRESHOLD
    context_covered: float = CONTEXT_COVERED_THRESHOLD
    flow_low: float = FLOW_BAND[0]
    flow_high: float = FLOW_BAND[1]
    # "default" or the corpus size it was measured on, for the EDL record.
    source: str = "default"

    @classmethod
    def from_corpus(
        cls,
        segments: Sequence[Segment],
        context_vecs: dict[str, list[float]] | None = None,
    ) -> Calibration:
        vectors = [s.embedding for s in segments if s.embedding]
        if len(vectors) < MIN_CALIBRATION_SEGMENTS:
            return cls()
        matrix = np.vstack([_unit(v) for v in vectors])
        pairs = matrix @ matrix.T
        upper = pairs[np.triu_indices(len(matrix), k=1)]

        low, high = (float(np.percentile(upper, p)) for p in FLOW_PCT)
        # A band with no width would make every flow reading a division by
        # near-zero; fall back rather than emit garbage.
        if not high > low:
            return cls()

        top = float(upper.max())
        redundancy = float(np.percentile(upper, REDUNDANCY_PCT))
        if redundancy >= top:
            # The top of the distribution is a tied block — an archive with
            # heavily repeated boilerplate, say. Leaving the cut at the
            # ceiling would mean no pair can ever exceed it and the term goes
            # inert, so split the difference between typical and maximal.
            redundancy = 0.5 * float(np.percentile(upper, 50.0)) + 0.5 * top

        covered = float(np.percentile(upper, FLOW_PCT[1]))
        if context_vecs:
            # How well prerequisites match this corpus at all. Anchoring on
            # their own distribution keeps the term honest when the model
            # embeds prerequisite-shaped text differently from transcript.
            ctx_matrix = np.vstack([_unit(v) for v in context_vecs.values()])
            best = (ctx_matrix @ matrix.T).max(axis=1)
            covered = float(np.percentile(best, CONTEXT_PCT))

        return cls(
            redundancy=redundancy,
            context_covered=covered,
            flow_low=low,
            flow_high=high,
            source=f"corpus:{len(vectors)}",
        )

    def as_dict(self) -> dict[str, float | str]:
        return {
            "redundancy": round(self.redundancy, 4),
            "context_covered": round(self.context_covered, 4),
            "flow_low": round(self.flow_low, 4),
            "flow_high": round(self.flow_high, 4),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> Calibration:
        if not data:
            return cls()
        known = {
            f: data[f]
            for f in ("redundancy", "context_covered", "flow_low", "flow_high")
            if f in data
        }
        return cls(**known, source=str(data.get("source", "restored")))


DEFAULT_CALIBRATION = Calibration()

WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    # Archive order. Sequencing is fixed, so the weights that remain are the
    # ones selection can still influence.
    "chronological": {
        "relevance": 0.30,
        "context_completeness": 0.22,
        "non_repetition": 0.16,
        "progression": 0.06,
        "escalation": 0.02,
        "callback": 0.02,
        "duration_fit": 0.14,
        "source_diversity": 0.08,
    },
    # Build to a peak.
    "escalation": {
        "relevance": 0.20,
        "context_completeness": 0.18,
        "non_repetition": 0.12,
        "progression": 0.12,
        "escalation": 0.20,
        "callback": 0.04,
        "duration_fit": 0.10,
        "source_diversity": 0.04,
    },
    # Plant early, pay off late.
    "callback": {
        "relevance": 0.20,
        "context_completeness": 0.18,
        "non_repetition": 0.10,
        "progression": 0.12,
        "escalation": 0.06,
        "callback": 0.20,
        "duration_fit": 0.10,
        "source_diversity": 0.04,
    },
    # The strawman the AI cuts must beat: relevance and nothing else.
    "semantic": {
        "relevance": 0.85,
        "context_completeness": 0.0,
        "non_repetition": 0.0,
        "progression": 0.0,
        "escalation": 0.0,
        "callback": 0.0,
        "duration_fit": 0.15,
        "source_diversity": 0.0,
    },
}


def profile_for(strategy: str) -> str:
    """The weight profile a strategy's output is scored under.

    `random` has no profile of its own: it is a control, and scoring it under
    the baseline's weights is what makes it comparable to `semantic`.
    """
    return strategy if strategy in WEIGHT_PROFILES else "semantic"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _unit(vec: Sequence[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    return arr / max(float(np.linalg.norm(arr)), 1e-8)


@dataclass
class PlanContext:
    """Everything scoring needs that is not the sequence itself."""

    query_vec: list[float]
    target_duration: float
    source_ordinals: dict[str, int] = field(default_factory=dict)
    # Ordered structural beats parsed from the user's prompt, embedded.
    beat_vecs: list[list[float]] = field(default_factory=list)
    beat_labels: list[str] = field(default_factory=list)
    # required_context string -> embedding, filled by `prepare_context`.
    context_vecs: dict[str, list[float]] = field(default_factory=dict)
    # Similarity cuts measured from the material in play, not hard-coded.
    calibration: Calibration = DEFAULT_CALIBRATION
    # Entities too common in this archive to signal a callback.
    common_entities: frozenset[str] = frozenset()

    def relevance_of(self, seg: Segment) -> float:
        if not seg.embedding:
            return 0.0
        return _clamp01(float(_unit(seg.embedding) @ _unit(self.query_vec)))


def prepare_context(
    ctx: PlanContext,
    segments: list[Segment],
    embed_fn: EmbedFn,
    *,
    calibrate: bool = True,
) -> PlanContext:
    """Embed every distinct required_context string once, then calibrate.

    `segments` should be the material actually in play, not the whole archive:
    "unusually similar" ought to mean unusual among the clips this mashup
    could draw on, which is a tighter, more topically clustered distribution
    than the archive at large.
    """
    needed = sorted(
        {c.strip() for s in segments for c in s.meta.required_context if c.strip()}
        - set(ctx.context_vecs)
    )
    if needed:
        for text, vec in zip(needed, embed_fn(needed), strict=True):
            ctx.context_vecs[text] = vec
    if calibrate:
        ctx.calibration = Calibration.from_corpus(segments, ctx.context_vecs)
    ctx.common_entities = common_entities(segments)
    return ctx


# An entity in more than this share of the material is the show's furniture —
# the host's name, the sponsor, the format's catchphrase — not a running gag.
COMMON_ENTITY_SHARE = 0.05


def common_entities(segments: Sequence[Segment], share: float = COMMON_ENTITY_SHARE) -> frozenset:
    """Entities frequent enough that sharing one says nothing."""
    if not segments:
        return frozenset()
    counts: Counter[str] = Counter()
    for seg in segments:
        # Per segment, not per mention: a clip repeating a name ten times
        # still only evidences that clip.
        counts.update({e.strip().lower() for e in seg.meta.entities if e.strip()})
    cutoff = max(2, math.ceil(share * len(segments)))
    return frozenset(name for name, n in counts.items() if n > cutoff)


# ---- individual terms ---------------------------------------------------


def term_relevance(seq: list[Segment], ctx: PlanContext) -> float:
    if not seq:
        return 0.0
    return float(np.mean([ctx.relevance_of(s) for s in seq]))


def term_context_completeness(seq: list[Segment], ctx: PlanContext) -> float:
    """Fraction of clips a viewer can follow at the point they appear."""
    if not seq:
        return 0.0
    scores: list[float] = []
    for i, seg in enumerate(seq):
        reqs = [c.strip() for c in seg.meta.required_context if c.strip()]
        if not reqs:
            scores.append(1.0)
            continue
        if i == 0:
            # Nothing precedes it, so the only thing that saves it is the
            # model judging it able to open cold.
            scores.append(1.0 if seg.meta.can_open else 0.0)
            continue
        prior = [_unit(p.embedding) for p in seq[:i] if p.embedding]
        if not prior:
            scores.append(0.0)
            continue
        prior_mat = np.vstack(prior)
        covered = 0
        for req in reqs:
            vec = ctx.context_vecs.get(req)
            if vec is None:
                continue
            if float(np.max(prior_mat @ _unit(vec))) >= ctx.calibration.context_covered:
                covered += 1
        scores.append(covered / len(reqs))
    return float(np.mean(scores))


def term_non_repetition(
    seq: list[Segment],
    sim: Callable[[Segment, Segment], float],
    calibration: Calibration = DEFAULT_CALIBRATION,
) -> float:
    """Penalises both near-duplicate material and topic monotony."""
    if len(seq) < 2:
        return 1.0
    cut = calibration.redundancy
    headroom = max(1.0 - cut, 1e-6)
    excesses: list[float] = []
    for i in range(len(seq)):
        for j in range(i + 1, len(seq)):
            s = sim(seq[i], seq[j])
            excesses.append(max(0.0, s - cut) / headroom)
    duplicate_penalty = float(np.mean(excesses))

    topics = [frozenset(t.lower() for t in s.meta.topic) for s in seq]
    seen: set[frozenset[str]] = set()
    repeats = 0
    for t in topics:
        if t and t in seen:
            repeats += 1
        seen.add(t)
    topic_penalty = repeats / len(seq)

    return _clamp01(1.0 - 0.7 * duplicate_penalty - 0.3 * topic_penalty)


def term_progression(
    seq: list[Segment],
    ctx: PlanContext,
    sim: Callable[[Segment, Segment], float],
) -> float:
    """How well the sequence moves somewhere.

    With an explicit requested structure ("start with education, escalate into
    career, finish with marriage") this measures beat order and coverage. With
    no structure it falls back to flow: neighbours should be related but not
    interchangeable.
    """
    if len(seq) < 2:
        return 1.0 if seq else 0.0

    if ctx.beat_vecs:
        beats = np.vstack([_unit(b) for b in ctx.beat_vecs])
        assigned = []
        for s in seq:
            if not s.embedding:
                assigned.append(0)
                continue
            assigned.append(int(np.argmax(beats @ _unit(s.embedding))))
        ordered = sum(1 for a, b in zip(assigned, assigned[1:], strict=False) if b >= a) / (
            len(assigned) - 1
        )
        coverage = len(set(assigned)) / len(ctx.beat_vecs)
        # Landing in the final beat is what makes a structure feel delivered.
        finished = 1.0 if assigned[-1] == len(ctx.beat_vecs) - 1 else 0.0
        return _clamp01(0.5 * ordered + 0.3 * coverage + 0.2 * finished)

    lo, hi = ctx.calibration.flow_low, ctx.calibration.flow_high
    mid = (lo + hi) / 2
    flows = []
    for a, b in zip(seq, seq[1:], strict=False):
        s = sim(a, b)
        # Triangular preference peaking in the middle of the band.
        flows.append(_clamp01(1.0 - abs(s - mid) / (hi - lo)))
    return float(np.mean(flows))


def term_escalation(seq: list[Segment]) -> float:
    """Rising intensity plus forward movement through the comedic arc."""
    if len(seq) < 2:
        return 0.5
    energies = np.asarray([s.meta.energy for s in seq], dtype=np.float64)
    positions = np.arange(len(seq), dtype=np.float64)
    if float(np.std(energies)) < 1e-6:
        energy_trend = 0.5
    else:
        corr = float(np.corrcoef(positions, energies)[0, 1])
        energy_trend = (corr + 1.0) / 2.0

    arcs = [s.meta.role.arc_index for s in seq]
    forward = sum(1 for a, b in zip(arcs, arcs[1:], strict=False) if b >= a) / (len(arcs) - 1)
    # A peak at the very end matters more than a smooth ramp.
    ends_high = 1.0 if energies[-1] >= float(np.max(energies)) - 0.05 else 0.0
    return _clamp01(0.5 * energy_trend + 0.3 * forward + 0.2 * ends_high)


def term_callback(seq: list[Segment], common_entities: frozenset[str] = frozenset()) -> float:
    """Rewards planted-then-paid-off material.

    Three things have to hold before a shared entity counts.

    It must span a gap — two adjacent clips about the same thing are
    continuation. It must span two different recordings: within one episode a
    repeated name is just the same conversation continuing, and rewarding it
    credits the planner for something the original edit already did. And the
    entity must not be one of `common_entities`, the names so frequent in the
    archive that sharing one says nothing — this corpus mentions "groucho" in
    96 segments and the sponsor in 48, so without that filter almost any pair
    of clips reads as a callback and the term rewards noise.
    """
    if len(seq) < 3:
        return 0.0
    ents = [
        {e.strip().lower() for e in s.meta.entities if e.strip()} - common_entities for s in seq
    ]

    def links(j: int, i: int) -> bool:
        return bool(ents[j] & ents[i]) and seq[j].source_id != seq[i].source_id

    hits = sum(1 for j in range(2, len(seq)) if any(links(j, i) for i in range(j - 1)))
    density = hits / (len(seq) - 2)

    bookend = 1.0 if links(len(seq) - 1, 0) else 0.0
    lands = 1.0 if seq[-1].meta.role in (Role.CALLBACK, Role.CLOSER) else 0.0
    return _clamp01(0.5 * density + 0.25 * bookend + 0.25 * lands)


def term_duration_fit(seq: list[Segment], target: float) -> float:
    if target <= 0:
        return 0.0
    total = sum(s.duration for s in seq)
    return _clamp01(1.0 - abs(total - target) / target)


def term_source_diversity(seq: list[Segment]) -> float:
    """Normalised entropy over source episodes.

    A 'mashup' drawn from two episodes is a supercut; the point is breadth.
    """
    if not seq:
        return 0.0
    counts = Counter(s.source_id for s in seq)
    if len(counts) == 1:
        return 0.0
    total = sum(counts.values())
    entropy = -sum((c / total) * math.log(c / total) for c in counts.values())
    return _clamp01(entropy / math.log(min(len(counts), total)))


# ---- aggregate ----------------------------------------------------------


def score_sequence(
    seq: list[Segment],
    ctx: PlanContext,
    sim: Callable[[Segment, Segment], float],
) -> ScoreTerms:
    return ScoreTerms(
        relevance=term_relevance(seq, ctx),
        context_completeness=term_context_completeness(seq, ctx),
        non_repetition=term_non_repetition(seq, sim, ctx.calibration),
        progression=term_progression(seq, ctx, sim),
        escalation=term_escalation(seq),
        callback=term_callback(seq, ctx.common_entities),
        duration_fit=term_duration_fit(seq, ctx.target_duration),
        source_diversity=term_source_diversity(seq),
    )


def total_score(terms: ScoreTerms, strategy: str) -> float:
    return terms.total(WEIGHT_PROFILES[strategy])
