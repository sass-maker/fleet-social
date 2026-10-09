"""Offline tests for segment enrichment. The Gateway is stubbed; no network."""

from __future__ import annotations

from typing import Any

from mashup.gateway import GatewayError
from mashup.models import Role, Segment, SegmentMeta
from mashup.segment.enrich import BATCH_SIZE, CONTEXT_CHARS, enrich_segments


class StubGateway:
    """Records every chat call and replays a scripted reply per call.

    Mirrors the real backends' contract: `chat_json_many` answers a list of
    conversations and returns `None` for any that failed, so a raising reply
    exercises the per-batch degradation rather than killing the run.
    """

    name = "stub"

    def __init__(self, replies: Any) -> None:
        self._replies = replies
        self.calls: list[list[dict[str, Any]]] = []

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        schema_hint: str,
        model: str | None = None,
        retries: int = 3,
    ) -> Any:
        self.calls.append(messages)
        if callable(self._replies):
            return self._replies(messages)
        return self._replies

    def chat_json_many(
        self,
        conversations: Any,
        *,
        schema_hint: str,
        concurrency: int = 4,
    ) -> list[Any]:
        out: list[Any] = []
        for messages in conversations:
            try:
                out.append(self.chat_json(messages, schema_hint=schema_hint))
            except GatewayError:
                out.append(None)
        return out

    @property
    def prompts(self) -> list[str]:
        return [m[-1]["content"] for m in self.calls]


def make_segment(i: int, *, source_id: str = "src1", text: str | None = None) -> Segment:
    return Segment(
        id=f"s{i}",
        source_id=source_id,
        start=float(i * 10),
        end=float(i * 10 + 9),
        text=text if text is not None else f"segment {i} body",
        cue_start=i * 2,
        cue_end=i * 2 + 1,
    )


def good_item(seg_id: str) -> dict[str, Any]:
    return {
        "id": seg_id,
        "topic": ["Airports", "luggage"],
        "role": "punchline",
        "summary": "He describes losing his bag.",
        "required_context": ["the earlier bit about the airline"],
        "energy": 0.8,
        "can_open": False,
        "can_end": True,
        "entities": ["the blue suitcase"],
    }


def echo_good(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reply with a valid item for every id mentioned in the prompt."""
    prompt = messages[-1]["content"]
    ids = [line.removeprefix("id: ") for line in prompt.splitlines() if line.startswith("id: ")]
    return [good_item(i) for i in ids]


def test_batches_segments_five_at_a_time() -> None:
    segments = [make_segment(i) for i in range(12)]
    gw = StubGateway(echo_good)

    enriched = enrich_segments(segments, gw, concurrency=2)

    assert BATCH_SIZE == 5
    assert len(gw.calls) == 3
    assert sorted(p.count("### ITEM ") for p in gw.prompts) == [2, 5, 5]
    assert [s.id for s in enriched] == [s.id for s in segments]


def test_parses_fields_into_segment_meta() -> None:
    segments = [make_segment(0)]
    enriched = enrich_segments(segments, StubGateway(echo_good))

    meta = enriched[0].meta
    assert meta.role is Role.PUNCHLINE
    assert meta.topic == ["airports", "luggage"]  # tags are normalised to lowercase
    assert meta.required_context == ["the earlier bit about the airline"]
    assert meta.energy == 0.8
    assert meta.can_end is True
    assert meta.entities == ["the blue suitcase"]
    # The input objects are not mutated.
    assert segments[0].meta == SegmentMeta()


def test_bad_item_falls_back_without_losing_the_batch() -> None:
    segments = [make_segment(i) for i in range(3)]
    reply = [
        good_item("s0"),
        {"id": "s1", "energy": 42.0, "role": "punchline"},  # energy is out of range
        good_item("s2"),
    ]
    enriched = enrich_segments(segments, StubGateway(reply))
    by_id = {s.id: s.meta for s in enriched}

    assert by_id["s0"].role is Role.PUNCHLINE
    assert by_id["s2"].role is Role.PUNCHLINE
    assert by_id["s1"] == SegmentMeta()  # neutral default, batch survives


def test_unknown_role_only_costs_the_role() -> None:
    segments = [make_segment(0)]
    item = good_item("s0") | {"role": "Zinger"}
    enriched = enrich_segments(segments, StubGateway([item]))

    assert enriched[0].meta.role is Role.DEVELOPMENT
    assert enriched[0].meta.summary == "He describes losing his bag."


def test_missing_item_gets_neutral_default() -> None:
    segments = [make_segment(i) for i in range(2)]
    enriched = enrich_segments(segments, StubGateway([good_item("s0")]))
    by_id = {s.id: s.meta for s in enriched}

    assert by_id["s0"].role is Role.PUNCHLINE
    assert by_id["s1"] == SegmentMeta()


def test_neighbouring_context_is_in_the_prompt_and_labelled() -> None:
    segments = [
        make_segment(0, text="A" * 400),
        make_segment(1, text="the middle bit"),
        make_segment(2, text="C" * 400),
    ]
    gw = StubGateway(echo_good)
    enrich_segments(segments, gw)

    prompt = gw.prompts[0]
    assert "CONTEXT BEFORE" in prompt and "CONTEXT AFTER" in prompt and "SEGMENT:" in prompt
    # Item 2's window is the tail of item 1 and the head of item 3, each clipped.
    lines = prompt.splitlines()
    before = next(line for line in lines if line.startswith("CONTEXT BEFORE") and "AAA" in line)
    after = next(line for line in lines if line.startswith("CONTEXT AFTER") and "CCC" in line)
    assert before.endswith(": " + "A" * CONTEXT_CHARS)
    assert after.endswith(": " + "C" * CONTEXT_CHARS)


def test_context_does_not_cross_sources() -> None:
    segments = [
        make_segment(0, source_id="src1", text="first source tail"),
        make_segment(1, source_id="src2", text="second source head"),
    ]
    gw = StubGateway(echo_good)
    enrich_segments(segments, gw)

    prompt = gw.prompts[0]
    assert prompt.count("(start of recording)") == 2
    assert prompt.count("(end of recording)") == 2


def test_progress_callback_reports_totals() -> None:
    segments = [make_segment(i) for i in range(7)]
    seen: list[tuple[int, int]] = []

    enrich_segments(
        segments,
        StubGateway(echo_good),
        concurrency=1,
        progress=lambda done, total: seen.append((done, total)),
    )

    assert [total for _, total in seen] == [7, 7]
    assert [done for done, _ in seen] == [5, 7]


def test_accepts_object_wrapped_array() -> None:
    segments = [make_segment(0)]
    enriched = enrich_segments(segments, StubGateway({"segments": [good_item("s0")]}))

    assert enriched[0].meta.role is Role.PUNCHLINE


def test_empty_input_makes_no_calls() -> None:
    gw = StubGateway(echo_good)
    assert enrich_segments([], gw) == []
    assert gw.calls == []


def test_failed_batch_does_not_discard_the_run() -> None:
    """One unroutable batch must not cost the whole archive.

    A 727-segment run died at 82% this way and lost every enriched segment.
    Failures now degrade to neutral metadata for that batch alone, so the rest
    persists and the failures are retried on the next run.
    """
    segments = [make_segment(i) for i in range(10)]
    calls: list[int] = []

    class FlakyGateway(StubGateway):
        def __init__(self) -> None:
            super().__init__(None)

        def chat_json(self, messages, *, schema_hint, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise GatewayError("POST /v1/chat/completions failed", status_code=400)
            return echo_good(messages)

    out = enrich_segments(segments, FlakyGateway(), concurrency=1, batch_size=5)
    summaries = [s.meta.summary for s in out]
    assert summaries.count("") == 5, "exactly the failed batch keeps default metadata"
    assert len(summaries) - summaries.count("") == 5, "the surviving batch is enriched"


# ---- entity hygiene ------------------------------------------------------


def entities_from(values: Any) -> list[str]:
    segments = [make_segment(0)]
    reply = [{**good_item("s0"), "entities": values}]
    return enrich_segments(segments, StubGateway(reply), batch_size=1)[0].meta.entities


def test_entities_are_lowercased() -> None:
    assert entities_from(["Bettina", "MR. SERIGNOLI"]) == ["bettina", "mr. serignoli"]


def test_bare_numbers_are_not_entities() -> None:
    """A local model kept offering ages and quiz answers as entities. Nothing
    numeric is ever what a later clip calls back to, and `term_callback`
    matching on '73' would be pure noise."""
    assert entities_from(["arresti", "73", "65", "1953", "12.5", "$40"]) == ["arresti"]


def test_a_name_containing_digits_survives() -> None:
    assert entities_from(["apollo 13", "route 66"]) == ["apollo 13", "route 66"]
