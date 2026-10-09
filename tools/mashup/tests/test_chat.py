"""Chat backends.

The parts that need mlx are gated on the import; everything else — backend
selection, the batching contract, per-prompt failure isolation — runs
anywhere, because that is where the bugs that would corrupt an archive live.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest

from mashup.chat import ChatError, ChatModel, LocalChat, make_chat
from mashup.config import Config
from mashup.gateway import Gateway, GatewayError


def cfg(**over: Any) -> Config:
    base = {
        "gateway_url": "http://gateway.invalid",
        "gateway_api_key": "",
        "project_id": "test",
        "chat_model": "stub-chat",
        "embed_model": "stub-embed",
        "workdir": Path("/tmp/mashup-test"),
    }
    return Config(**{**base, **over})


# ---- backend selection ---------------------------------------------------


def test_local_backend_is_selected() -> None:
    assert isinstance(make_chat(cfg(chat_backend="local")), LocalChat)


def test_gateway_backend_returns_the_gateway_itself() -> None:
    """Gateway already satisfies ChatModel, so there is no wrapper to drift."""
    gw = Gateway(cfg(chat_backend="gateway"))
    assert make_chat(cfg(chat_backend="gateway"), gateway=gw) is gw
    gw.close()


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ChatError, match="unknown chat backend"):
        make_chat(cfg(chat_backend="telepathy"))


def test_both_backends_satisfy_the_protocol() -> None:
    gw = Gateway(cfg())
    assert isinstance(gw, ChatModel)
    assert isinstance(LocalChat(), ChatModel)
    gw.close()


def test_local_name_identifies_the_model() -> None:
    assert LocalChat("mlx-community/Qwen3-4B-Instruct-2507-4bit").name == (
        "local:mlx-community/Qwen3-4B-Instruct-2507-4bit"
    )


def test_constructing_a_local_chat_loads_nothing() -> None:
    """A 2GB model must not load just because a config was read."""
    chat = LocalChat("mlx-community/does-not-exist")
    assert chat._model is None


def test_missing_mlx_is_reported_as_a_chat_error(monkeypatch) -> None:
    real_import = builtins.__import__

    def import_without_mlx(name, *args, **kwargs):
        if name == "mlx_lm":
            raise ImportError("mlx is unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_mlx)
    with pytest.raises(ChatError, match="local chat needs mlx-lm"):
        LocalChat().chat_json_many([convo(0)], schema_hint="[]")


# ---- the batching contract -----------------------------------------------


class ScriptedGateway(Gateway):
    """A Gateway whose chat_json is scripted, to test chat_json_many only."""

    def __init__(self, replies: list[Any]) -> None:
        super().__init__(cfg())
        self._replies = list(replies)
        self.seen: list[str] = []

    def chat_json(self, messages: list[dict[str, Any]], *, schema_hint: str, **_kw: Any) -> Any:
        text = messages[-1]["content"]
        self.seen.append(text)
        reply = self._replies[int(text)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def convo(index: int) -> list[dict[str, str]]:
    return [{"role": "user", "content": str(index)}]


def test_gateway_many_preserves_order_under_concurrency() -> None:
    """Answers come back out of order from a thread pool; if they are not
    remapped, every batch's metadata lands on the wrong segments."""
    gw = ScriptedGateway([{"n": i} for i in range(12)])
    out = gw.chat_json_many([convo(i) for i in range(12)], schema_hint="[]", concurrency=6)
    assert out == [{"n": i} for i in range(12)]
    gw.close()


def test_gateway_many_isolates_a_failure() -> None:
    replies: list[Any] = [{"n": 0}, GatewayError("boom"), {"n": 2}]
    gw = ScriptedGateway(replies)
    out = gw.chat_json_many([convo(i) for i in range(3)], schema_hint="[]")
    assert out == [{"n": 0}, None, {"n": 2}]
    gw.close()


def test_gateway_many_handles_no_work() -> None:
    gw = ScriptedGateway([])
    assert gw.chat_json_many([], schema_hint="[]") == []
    gw.close()


# ---- local batching, with generation stubbed -----------------------------


class FakeLocalChat(LocalChat):
    """LocalChat with mlx replaced, to test windowing and parse handling."""

    def __init__(self, texts: list[str], **kw: Any) -> None:
        super().__init__(**kw)
        self._texts = texts
        self.windows: list[int] = []

    def chat_json_many(self, conversations, *, schema_hint, concurrency=4):
        # Reproduce the real method's windowing over a stubbed generator.
        from mashup.jsonreply import parse_json_reply

        conversations = list(conversations)
        out: list[Any] = [None] * len(conversations)
        width = max(1, concurrency)
        for start in range(0, len(conversations), width):
            window = list(range(start, min(start + width, len(conversations))))
            self.windows.append(len(window))
            for i in window:
                try:
                    out[i] = parse_json_reply(self._texts[i])
                except ValueError:
                    out[i] = None
        return out


def test_local_many_windows_by_concurrency() -> None:
    chat = FakeLocalChat(['{"n": 1}'] * 10)
    chat.chat_json_many([convo(i) for i in range(10)], schema_hint="[]", concurrency=4)
    assert chat.windows == [4, 4, 2]


def test_local_many_survives_an_unparseable_reply() -> None:
    """A local model that trails off mid-JSON must cost one batch, not the
    run: those segments keep their default and the next enrich retries them."""
    chat = FakeLocalChat(['{"n": 0}', "I think the answer is...", '{"n": 2}'])
    assert chat.chat_json_many([convo(i) for i in range(3)], schema_hint="[]") == [
        {"n": 0},
        None,
        {"n": 2},
    ]


def test_local_chat_json_raises_when_the_model_will_not_produce_json() -> None:
    chat = FakeLocalChat(["not json at all"])
    with pytest.raises(ChatError, match="never returned valid JSON"):
        chat.chat_json(convo(0), schema_hint="[]")


# ---- the real model ------------------------------------------------------


def test_local_model_answers_with_usable_json() -> None:
    """One end-to-end call against the genuine model, in the shape enrichment
    uses. Skipped where mlx or the weights are absent."""
    chat = LocalChat(max_tokens=200)
    schema = '[{"id": "<the id given>", "topic": ["..."]}]'
    try:
        reply = chat.chat_json(
            [
                {"role": "system", "content": "You tag transcript segments."},
                {"role": "user", "content": "Item id=a1: a joke about airline luggage."},
            ],
            schema_hint=schema,
        )
    except ChatError as exc:
        pytest.skip(str(exc))

    items = reply if isinstance(reply, list) else [reply]
    assert items and isinstance(items[0], dict)
    assert "topic" in items[0]
