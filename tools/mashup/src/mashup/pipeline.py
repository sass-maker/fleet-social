"""Stage orchestration.

Each stage is separately resumable and writes its result to the store, because
the expensive stages (transcription, enrichment, embedding) must never be paid
for twice while iterating on the cheap one (planning).
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from mashup.chat import make_chat
from mashup.config import Config
from mashup.content_integrity import remove_snapped_render_overlap
from mashup.embedding import make_embedder
from mashup.gateway import Gateway
from mashup.ingest import ingest_archive
from mashup.models import EDL, Clip, Segment, Source
from mashup.plan.planner import PlanResult, plan, plan_random, plan_semantic, rescore
from mashup.plan.prompt import MashupRequest, parse_request
from mashup.plan.score import Calibration, PlanContext, prepare_context
from mashup.render.boundaries import detect_silences, snap_boundaries
from mashup.retrieve import (
    Candidate,
    Coverage,
    Retriever,
    embed_segments,
    nonsense_probes,
)
from mashup.segment.editorial import (
    build_editorial_candidates,
    review_candidate_boundaries,
)
from mashup.segment.enrich import enrich_segments
from mashup.segment.splitter import split_source
from mashup.shorts import (
    ShortPlanningError,
    build_short_candidates,
    review_short_candidates,
    select_distinct_segments,
    validate_short_duration,
)
from mashup.store import Store

AI_STRATEGIES = ("chronological", "escalation", "callback")

# Candidate clips retrieved before planning. Not uniformly better when
# widened: measured on the dev archive it lifts chronological, which can
# only walk forward through archive order and starves on a small pool, and
# depresses callback. So it stays a per-run choice rather than a tuned
# constant.
DEFAULT_POOL = 40


def ingest(
    archive_dir: Path,
    cfg: Config,
    *,
    allow_transcribe: bool = True,
    progress=None,
) -> dict[str, int]:
    """Ingest media + subtitles and split into segments."""
    cfg.ensure_dirs()
    skipped_sources = 0

    def report_unreadable(path: Path, error: Exception) -> None:
        nonlocal skipped_sources
        skipped_sources += 1
        if progress is not None:
            progress(f"Skipped unreadable source {path.name}: {error}")

    with Store(cfg.db_path) as store:
        items = ingest_archive(
            archive_dir,
            workdir=cfg.workdir,
            allow_transcribe=allow_transcribe,
            skip_unreadable=True,
            on_error=report_unreadable,
        )
        for source, cues in items:
            store.upsert_source(source, cues)
            store.replace_segments(source.id, split_source(source.id, cues))
        counts = store.counts()
        counts["skipped_sources"] = skipped_sources
        return counts


# Segments per store write during enrichment. Small enough that a crash costs
# little, large enough that the write is not the bottleneck.
ENRICH_CHECKPOINT = 50


def enrich(cfg: Config, *, concurrency: int = 4, progress=None) -> dict[str, int]:
    with Store(cfg.db_path) as store:
        segments = store.get_segments(with_embeddings=False)
        todo = [s for s in segments if not s.meta.summary]
        if not todo:
            return store.counts()

        chat = make_chat(cfg, gateway=Gateway(cfg) if cfg.chat_backend == "gateway" else None)
        done = 0
        # Checkpoint to the store as we go. Holding every result until the end
        # meant one failed batch discarded the entire archive's work.
        for i in range(0, len(todo), ENRICH_CHECKPOINT):
            chunk = todo[i : i + ENRICH_CHECKPOINT]

            def chunk_progress(c: int, _t: int, base: int = done) -> None:
                if progress is not None:
                    progress(base + c, len(todo))

            # enrich_segments returns copies rather than mutating in place, so
            # the return value is the only thing carrying the new metadata.
            enriched = enrich_segments(
                chunk, chat, concurrency=concurrency, progress=chunk_progress
            )
            # Only persist segments that actually came back enriched, so a
            # failed batch stays on the todo list for the next run.
            store.update_segment_meta([s for s in enriched if s.meta.summary])
            done += len(chunk)
        return store.counts()


def embed(cfg: Config, *, progress=None, reset: bool = False, notice=None) -> dict[str, int]:
    # Construct the gateway through the module-level name so the backend stays
    # substitutable, and only when it is the backend actually in use.
    gw = Gateway(cfg) if cfg.embed_backend == "gateway" else None
    embedder = make_embedder(cfg, gateway=gw)
    with Store(cfg.db_path) as store:
        stale = [m for m in store.embedding_models() if m != embedder.name]
        if reset or stale:
            if stale and not reset and notice:
                # Not a warning to be dismissed: vectors from two models are
                # not comparable, so keeping them would quietly poison every
                # similarity in the pipeline. Re-embedding is the only
                # correct move, and locally it costs seconds.
                notice(
                    f"re-embedding: stored vectors came from {', '.join(repr(m) for m in stale)}, "
                    f"now using {embedder.name!r}"
                )
            store.clear_embeddings()
        segments = store.get_segments()
        embed_segments(segments, embedder, progress=progress)
        store.update_segment_embeddings(segments, embedder.name)
        return store.counts()


def _clip_from_segment(
    index: int,
    seg: Segment,
    source: Source,
    store: Store,
    cfg: Config,
    *,
    snap: bool,
    crossfade: float,
) -> Clip:
    render_start, render_end = seg.start, seg.end
    if snap:
        silences = detect_silences(Path(source.path), cache_dir=cfg.cache_dir)
        cues = store.get_cues(seg.source_id)
        render_start, render_end = snap_boundaries(seg.start, seg.end, silences=silences, cues=cues)
    return Clip(
        index=index,
        segment_id=seg.anchor_segment_id or seg.id,
        segment_ids=list(seg.member_segment_ids or [seg.id]),
        source_id=seg.source_id,
        source_title=source.title,
        source_path=source.path,
        start=seg.start,
        end=seg.end,
        render_start=render_start,
        render_end=render_end,
        text=seg.text,
        summary=seg.meta.summary,
        role=seg.meta.role,
        energy=seg.meta.energy,
        topics=list(seg.meta.topic),
        transition="crossfade" if crossfade > 0 else "cut",
    )


def result_to_edl(
    result: PlanResult,
    request: MashupRequest,
    cfg: Config,
    store: Store,
    *,
    target: float,
    snap: bool = True,
    crossfade: float = 0.0,
    calibration: Calibration | None = None,
) -> EDL:
    sources = {s.id: s for s in store.get_sources()}
    clips = [
        _clip_from_segment(
            i, seg, sources[seg.source_id], store, cfg, snap=snap, crossfade=crossfade
        )
        for i, seg in enumerate(result.sequence)
    ]
    clips = remove_snapped_render_overlap(clips)
    from mashup.plan.score import WEIGHT_PROFILES

    return EDL(
        calibration=(calibration or Calibration()).as_dict(),
        strategy=result.strategy,
        prompt=request.prompt,
        target_duration=target,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        clips=clips,
        score=result.score,
        terms=result.terms,
        weights=WEIGHT_PROFILES.get(result.strategy, {}),
        rationale=result.rationale,
    )


@dataclass
class Planning:
    """Everything the planners share, built once per run."""

    store: Store
    request: MashupRequest
    ctx: PlanContext
    retriever: Retriever
    candidates: list[Candidate]
    baseline_candidates: list[Candidate]
    callback_pool: list[Candidate]
    coverage: Coverage

    def pool_for(self, strategy: str) -> list[Candidate]:
        return self.callback_pool if strategy == "callback" else self.candidates


@contextmanager
def planning_session(
    prompt: str,
    cfg: Config,
    *,
    target: float,
    pool: int,
    editorial: bool = True,
) -> Iterator[Planning]:
    """Open the store, embed the brief, retrieve, and calibrate.

    Shared by every entry point that plans, so a matched-set run and a
    five-condition run cannot drift apart in how they set the objective up.
    """
    # Local planning does not need chat. Without a key, use the deterministic
    # brief parser instead of constructing a doomed bearer-auth request.
    gw = Gateway(cfg) if cfg.gateway_api_key else None
    embedder = make_embedder(cfg, gateway=gw)
    request = parse_request(prompt, gw)

    # The brief, its beats and the required-context strings are all questions
    # asked of the corpus, so they take the query side of an asymmetric model.
    def embed_query(texts: list[str]) -> list[list[float]]:
        return embedder.embed(texts, kind="query")

    with Store(cfg.db_path) as store:
        segments = store.get_segments()
        retriever = Retriever(segments)
        sources = {s.id: s for s in store.get_sources()}

        query_vec = embed_query([request.query])[0]
        beat_vecs = embed_query(request.beats) if request.beats else []

        ctx = PlanContext(
            query_vec=query_vec,
            target_duration=target,
            source_ordinals={sid: s.ordinal for sid, s in sources.items()},
            beat_vecs=beat_vecs,
            beat_labels=list(request.beats),
        )
        # The AI strategies plan over an MMR-diversified pool, because their
        # job is to build variety out of it.
        candidates = retriever.mmr(query_vec, top_k=pool)

        # The baselines deliberately do NOT get MMR. "Retrieve the most
        # relevant clips and join them" is the thing being argued against, so
        # handing it free diversity would soften the very comparison the
        # project exists to make.
        baseline_candidates = retriever.search(query_vec, top_k=pool)

        # The callback strategy needs material MMR deliberately removed, so it
        # plans over the diversified pool plus the entity-linked clips that
        # make a payoff possible at all.
        from mashup.plan.score import common_entities

        callback_pool = retriever.entity_expansion(
            candidates,
            query_vec,
            common=common_entities([c.segment for c in candidates]),
        )
        if editorial:
            chat = make_chat(
                cfg,
                gateway=Gateway(cfg) if cfg.chat_backend == "gateway" else None,
            )
            reviews = review_candidate_boundaries(
                [*candidates, *baseline_candidates, *callback_pool],
                segments,
                chat,
                cfg.cache_dir,
            )
            candidates = build_editorial_candidates(
                candidates,
                segments,
                query_vec,
                label="AI",
                reviews=reviews,
            )
            baseline_candidates = build_editorial_candidates(
                baseline_candidates,
                segments,
                query_vec,
                label="baseline",
                reviews=reviews,
            )
            callback_pool = build_editorial_candidates(
                callback_pool,
                segments,
                query_vec,
                label="callback",
                reviews=reviews,
            )

        ctx = prepare_context(
            ctx,
            [c.segment for c in candidates]
            + [c.segment for c in baseline_candidates]
            + [c.segment for c in callback_pool],
            # A required_context string ("the audience knows he is a plumber")
            # is a statement compared against other transcript, not a search
            # intent, so it takes the document side of an asymmetric model.
            lambda texts: embedder.embed(texts, kind="document"),
        )
        yield Planning(
            store=store,
            request=request,
            ctx=ctx,
            retriever=retriever,
            candidates=candidates,
            baseline_candidates=baseline_candidates,
            callback_pool=callback_pool,
            coverage=retriever.coverage(query_vec, embed_query(nonsense_probes())),
        )


def check_coverage(prompt: str, cfg: Config) -> Coverage:
    """Whether the archive holds material on this topic at all.

    Cheap relative to planning, and worth running first: a brief the archive
    cannot serve still produces five confident-looking variants, all of them
    built from clips no better matched than random text would have found.
    """
    with planning_session(prompt, cfg, target=1.0, pool=1, editorial=False) as session:
        return session.coverage


def make_mashups(
    prompt: str,
    cfg: Config,
    *,
    target: float,
    strategies: tuple[str, ...] = AI_STRATEGIES,
    include_baselines: bool = False,
    pool: int = DEFAULT_POOL,
    snap: bool = True,
    crossfade: float = 0.0,
) -> list[EDL]:
    """Plan one EDL per strategy from an already-enriched archive."""
    with planning_session(prompt, cfg, target=target, pool=pool) as session:
        ctx, retriever = session.ctx, session.retriever
        results = [plan(s, session.pool_for(s), ctx, retriever.pairwise) for s in strategies]
        if include_baselines:
            results.append(plan_semantic(session.baseline_candidates, ctx, retriever.pairwise))
            results.append(plan_random(session.baseline_candidates, ctx, retriever.pairwise))

        return [
            result_to_edl(
                r,
                session.request,
                cfg,
                session.store,
                target=target,
                snap=snap,
                crossfade=crossfade,
                calibration=ctx.calibration,
            )
            for r in results
        ]


def make_short(
    prompt: str,
    cfg: Config,
    *,
    target: float = 45.0,
    pool: int = DEFAULT_POOL,
    crossfade: float = 0.0,
) -> EDL:
    """Select one complete cue-level 30–60 second cut."""
    validate_short_duration(target)
    with planning_session(prompt, cfg, target=target, pool=pool, editorial=False) as session:
        cues_by_source = {
            source.id: session.store.get_cues(source.id) for source in session.store.get_sources()
        }
        candidates = build_short_candidates(
            session.candidates,
            cues_by_source,
            target=target,
        )
        chat = make_chat(
            cfg,
            gateway=Gateway(cfg) if cfg.chat_backend == "gateway" else None,
        )
        reviewed = review_short_candidates(
            candidates,
            cues_by_source,
            chat,
            cfg.cache_dir,
        )
        if not reviewed:
            raise ShortPlanningError("no short window cleared the opening and payoff review")
        chosen = reviewed[0]
        result = plan(
            "escalation",
            [chosen.candidate],
            session.ctx,
            session.retriever.pairwise,
            max_clips=1,
        )
        edl = result_to_edl(
            result,
            session.request,
            cfg,
            session.store,
            target=target,
            # Cue boundaries are the duration contract. Outward snapping could
            # turn a valid 60-second result into an invalid short.
            snap=False,
            crossfade=crossfade,
            calibration=session.ctx.calibration,
        )
        return edl.model_copy(
            update={
                "strategy": "short",
                "short_review": chosen.review,
                "rationale": [
                    "short-form: one contiguous cue-level source window",
                    f"short editorial: hook {chosen.review.hook_strength:.2f}, "
                    f"payoff {chosen.review.payoff_strength:.2f}",
                    chosen.review.reason,
                    *edl.rationale,
                ],
            }
        )


def make_short_batch(
    prompt: str,
    cfg: Config,
    *,
    count: int = 5,
    target: float = 45.0,
    pool: int = DEFAULT_POOL,
) -> list[EDL]:
    """Select a ranked batch of complete, pairwise non-overlapping short cuts."""
    validate_short_duration(target)
    if not 3 <= count <= 5:
        raise ValueError("short batch count must be between 3 and 5")
    with planning_session(prompt, cfg, target=target, pool=pool, editorial=False) as session:
        cues_by_source = {
            source.id: session.store.get_cues(source.id) for source in session.store.get_sources()
        }
        candidates = build_short_candidates(
            session.candidates,
            cues_by_source,
            target=target,
        )
        chat = make_chat(
            cfg,
            gateway=Gateway(cfg) if cfg.chat_backend == "gateway" else None,
        )
        reviewed = review_short_candidates(
            candidates,
            cues_by_source,
            chat,
            cfg.cache_dir,
        )
        selected_segments = select_distinct_segments(
            [item.candidate.segment for item in reviewed],
            count=count,
        )
        selected_ids = {segment.id for segment in selected_segments}
        selected_reviews = [item for item in reviewed if item.candidate.segment.id in selected_ids][
            :count
        ]

        edls: list[EDL] = []
        for index, reviewed_short in enumerate(selected_reviews, start=1):
            result = plan(
                "escalation",
                [reviewed_short.candidate],
                session.ctx,
                session.retriever.pairwise,
                max_clips=1,
            )
            edl = result_to_edl(
                result,
                session.request,
                cfg,
                session.store,
                target=target,
                snap=False,
                crossfade=0.0,
                calibration=session.ctx.calibration,
            )
            edls.append(
                edl.model_copy(
                    update={
                        "strategy": f"short-{index:02d}",
                        "short_review": reviewed_short.review,
                        "rationale": [
                            f"short batch: candidate {index} of {count}, distinct source window",
                            f"short editorial: hook {reviewed_short.review.hook_strength:.2f}, "
                            f"payoff {reviewed_short.review.payoff_strength:.2f}",
                            reviewed_short.review.reason,
                            *edl.rationale,
                        ],
                    }
                )
            )
        return edls


# The two orders compared when sequencing is tested on its own.
MATCHED_ARMS = ("planned", "shuffled")
# Shuffles drawn to characterise "an arbitrary order" before one is chosen.
MATCHED_SHUFFLES = 200


@dataclass
class MatchedPair:
    """The two arms plus where each sits among arbitrary orders."""

    planned: EDL
    shuffled: EDL
    stats: dict[str, float | int]

    def __getitem__(self, arm: str) -> EDL:
        return {"planned": self.planned, "shuffled": self.shuffled}[arm]


def make_matched_pair(
    prompt: str,
    cfg: Config,
    *,
    target: float,
    strategy: str = "escalation",
    pool: int = DEFAULT_POOL,
    seed: int = 0,
    snap: bool = True,
    crossfade: float = 0.0,
    shuffles: int = MATCHED_SHUFFLES,
) -> MatchedPair:
    """One clip set, two orders — the isolation test for sequencing.

    The five-condition experiment compares variants built from *different*
    clips: measured on the dev archive, chronological shared 0-5% of its
    material with the other four. A viewer preferring it there says nothing
    about ordering, because selection is confounded with it.

    This holds the clips fixed and varies only their order, so a preference
    is attributable to sequencing and nothing else. Both arms are scored
    under the same weight profile, so their scores are directly comparable —
    unlike the five-condition run, where the baselines use a profile that
    cannot see order at all.

    The shuffled arm is the *median* of many draws rather than the first one,
    because a single draw is a lottery on a large discrete bonus. Only 19% of
    this archive's segments are marked `can_end`, so most orders take the
    unfinished-ending penalty and a few do not; the first seed tried landed a
    shuffle in the top 14% purely by ending well, which would have handed
    viewers a comparator that is not arbitrary at all. Picking the median
    keeps the comparator random but typical. It does mean the objective
    chooses which random order is representative — if the objective is wrong
    about endings, so is that choice, which is why both percentiles are
    recorded rather than just the winner.
    """
    with planning_session(prompt, cfg, target=target, pool=pool) as session:
        ctx, retriever = session.ctx, session.retriever
        planned = plan(strategy, session.pool_for(strategy), ctx, retriever.pairwise)

        def reorder(seq: list[Segment], note: str) -> PlanResult:
            # Rescore rather than copy: an arm's recorded terms have to
            # describe the order it is actually in, or the EDL lies about
            # itself. `rescore` applies the same ending penalty `plan` does,
            # so the two arms are on one scale.
            return rescore(
                replace(planned, sequence=seq, rationale=[note]), ctx, retriever.pairwise
            )

        rng = random.Random(seed)
        draws: list[PlanResult] = []
        for _ in range(shuffles):
            seq = list(planned.sequence)
            rng.shuffle(seq)
            draws.append(reorder(seq, "same clips, arbitrary order"))
        draws.sort(key=lambda r: r.score)
        shuffled = draws[len(draws) // 2]

        scores = [r.score for r in draws]
        below = sum(1 for s in scores if s < planned.score)
        stats = {
            "shuffles": len(draws),
            "planned_score": round(planned.score, 4),
            "shuffled_score": round(shuffled.score, 4),
            "planned_percentile": round(100 * below / len(draws), 1),
            "shuffle_min": round(min(scores), 4),
            "shuffle_max": round(max(scores), 4),
        }

        return MatchedPair(
            **{
                arm: result_to_edl(
                    result,
                    session.request,
                    cfg,
                    session.store,
                    target=target,
                    snap=snap,
                    crossfade=crossfade,
                    calibration=ctx.calibration,
                )
                for arm, result in zip(MATCHED_ARMS, (planned, shuffled), strict=True)
            },
            stats=stats,
        )
