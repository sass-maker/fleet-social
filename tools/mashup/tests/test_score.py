from __future__ import annotations

from conftest import make_segment, unit_vec

from mashup.models import Role
from mashup.plan.score import (
    PlanContext,
    prepare_context,
    score_sequence,
    term_callback,
    term_context_completeness,
    term_duration_fit,
    term_escalation,
    term_non_repetition,
    term_source_diversity,
)


def ctx(target: float = 300.0, **kw) -> PlanContext:
    return PlanContext(query_vec=unit_vec(0.0), target_duration=target, **kw)


def test_relevance_rewards_alignment_with_query():
    close = make_segment("a", angle=0.05)
    far = make_segment("b", angle=1.4)
    c = ctx()
    assert c.relevance_of(close) > c.relevance_of(far)


def test_context_completeness_penalises_unmet_prerequisites(cosine_sim):
    dependent = make_segment("b", required_context=["the bit about the violin"])
    c = ctx()
    # No prior clip at all, and it cannot open cold.
    assert term_context_completeness([dependent], c) == 0.0

    opener = make_segment("a", required_context=["the violin"], can_open=True)
    assert term_context_completeness([opener], c) == 1.0


def test_context_completeness_satisfied_by_earlier_similar_clip():
    setup = make_segment("a", angle=0.0)
    dependent = make_segment("b", angle=0.02, required_context=["the violin bit"])
    c = ctx()
    c.context_vecs["the violin bit"] = unit_vec(0.0)  # matches the setup clip
    assert term_context_completeness([setup, dependent], c) == 1.0

    # Same prerequisite, but the earlier clip is about something else. The
    # term averages over the whole sequence, so the self-contained first clip
    # still scores 1.0 and only the dependent clip scores 0.
    unrelated = make_segment("c", angle=1.5)
    assert term_context_completeness([unrelated, dependent], c) == 0.5


def test_prepare_context_embeds_each_distinct_string_once():
    segs = [
        make_segment("a", required_context=["x"]),
        make_segment("b", required_context=["x", "y"]),
    ]
    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [unit_vec(0.0) for _ in texts]

    c = prepare_context(ctx(), segs, fake_embed)
    assert sorted(c.context_vecs) == ["x", "y"]
    assert calls == [["x", "y"]], "each distinct prerequisite embedded exactly once"


def test_non_repetition_punishes_duplicates(cosine_sim):
    a = make_segment("a", angle=0.0, topics=["parents"])
    duplicate = make_segment("b", angle=0.001, topics=["parents"])
    varied = make_segment("c", angle=1.2, topics=["marriage"])
    assert term_non_repetition([a, duplicate], cosine_sim) < term_non_repetition(
        [a, varied], cosine_sim
    )


def test_escalation_prefers_rising_energy():
    rising = [make_segment(str(i), energy=0.2 + 0.2 * i) for i in range(4)]
    falling = list(reversed(rising))
    assert term_escalation(rising) > term_escalation(falling)


def test_escalation_rewards_forward_role_arc():
    arc = [
        make_segment("a", role=Role.SETUP, energy=0.5),
        make_segment("b", role=Role.DEVELOPMENT, energy=0.5),
        make_segment("c", role=Role.PUNCHLINE, energy=0.5),
    ]
    scrambled = [arc[2], arc[0], arc[1]]
    assert term_escalation(arc) > term_escalation(scrambled)


def test_callback_requires_a_gap():
    # Shared entity in adjacent clips is continuation, not a callback.
    adjacent = [
        make_segment("a", source_id="ep01", entities=["violin"]),
        make_segment("b", source_id="ep02", entities=["violin"]),
        make_segment("c", source_id="ep03", entities=["vault"]),
    ]
    planted = [
        make_segment("a", source_id="ep01", entities=["violin"]),
        make_segment("b", source_id="ep02", entities=["vault"]),
        make_segment("c", source_id="ep03", entities=["violin"]),
    ]
    assert term_callback(planted) > term_callback(adjacent)


def test_callback_rewards_bookending():
    bookend = [
        make_segment("a", source_id="ep01", entities=["violin"]),
        make_segment("b", source_id="ep02", entities=["vault"]),
        make_segment("c", source_id="ep03", entities=["violin"], role=Role.CALLBACK),
    ]
    assert term_callback(bookend) > 0.7


def test_callback_ignores_a_repeat_inside_one_recording():
    """A name recurring within a single episode is the original conversation
    continuing, not something the planner built. Counting it let the random
    control outscore the callback strategy on callback."""
    same_episode = [
        make_segment("a", source_id="ep01", entities=["violin"]),
        make_segment("b", source_id="ep01", entities=["vault"]),
        make_segment("c", source_id="ep01", entities=["violin"], role=Role.CALLBACK),
    ]
    woven = [
        make_segment("a", source_id="ep01", entities=["violin"]),
        make_segment("b", source_id="ep02", entities=["vault"]),
        make_segment("c", source_id="ep03", entities=["violin"], role=Role.CALLBACK),
    ]
    assert term_callback(same_episode) < term_callback(woven)


def test_callback_ignores_the_archives_boilerplate():
    """Every episode names the host and the sponsor; treating that as a plant
    and payoff would score any two clips as a callback."""
    boilerplate = [
        make_segment("a", source_id="ep01", entities=["groucho"]),
        make_segment("b", source_id="ep02", entities=["vault"]),
        make_segment("c", source_id="ep03", entities=["groucho"]),
    ]
    common = frozenset({"groucho"})
    assert term_callback(boilerplate, common) < term_callback(boilerplate)


def test_duration_fit_peaks_at_target():
    exact = [make_segment("a", duration=300.0)]
    short = [make_segment("a", duration=150.0)]
    assert term_duration_fit(exact, 300.0) == 1.0
    assert term_duration_fit(short, 300.0) == 0.5


def test_source_diversity_rewards_breadth():
    one_source = [make_segment(str(i), source_id="ep01") for i in range(4)]
    many = [make_segment(str(i), source_id=f"ep{i:02d}") for i in range(4)]
    assert term_source_diversity(one_source) == 0.0
    assert term_source_diversity(many) > 0.9


def test_progression_uses_beats_when_supplied(cosine_sim):
    # Beat 0 at angle 0, beat 1 at angle 1.0.
    c = ctx(beat_vecs=[unit_vec(0.0), unit_vec(1.0)], beat_labels=["school", "marriage"])
    in_order = [make_segment("a", angle=0.0), make_segment("b", angle=1.0)]
    reversed_order = [make_segment("a", angle=1.0), make_segment("b", angle=0.0)]
    from mashup.plan.score import term_progression

    assert term_progression(in_order, c, cosine_sim) > term_progression(
        reversed_order, c, cosine_sim
    )


def test_score_sequence_returns_all_terms(cosine_sim):
    seq = [make_segment("a", can_open=True), make_segment("b", can_end=True)]
    terms = score_sequence(seq, ctx(), cosine_sim)
    for field in terms.model_dump():
        assert 0.0 <= getattr(terms, field) <= 1.0, field
