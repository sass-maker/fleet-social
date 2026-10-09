"""Offline tests for the gateway client. No test here may touch the network."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from mashup.config import Config
from mashup.gateway import Gateway, GatewayError
from mashup.jsonreply import parse_json_reply


def make_config(tmp_path: Path) -> Config:
    return Config(
        gateway_url="https://gateway.test",
        gateway_api_key="key-123",
        project_id="mashup-test",
        chat_model="auto",
        embed_model="gemini-embedding-001",
        workdir=tmp_path,
    )


def chat_reply(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def make_gateway(
    tmp_path: Path,
    handler,
    *,
    use_cache: bool = False,
    retry_attempts: int = 4,
) -> Gateway:
    return Gateway(
        make_config(tmp_path),
        use_cache=use_cache,
        transport=httpx.MockTransport(handler),
        retry_attempts=retry_attempts,
        retry_wait=0.0,  # keep retry tests instant
        retry_max_wait=0.0,
    )


def test_sends_bearer_token_without_gateway_metadata(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=chat_reply("ok"))

    with make_gateway(tmp_path, handler) as gw:
        assert gw.chat([{"role": "user", "content": "hi"}]) == "ok"

    request = seen[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer key-123"
    body = json.loads(request.content)
    assert "project_id" not in body
    assert body["model"] == "auto"


def test_empty_key_omits_invalid_authorization_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    cfg = replace(make_config(tmp_path), gateway_api_key="")

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, text="missing key")

    with (
        Gateway(
            cfg,
            use_cache=False,
            transport=httpx.MockTransport(handler),
            retry_attempts=1,
            retry_wait=0.0,
            retry_max_wait=0.0,
        ) as gw,
        pytest.raises(GatewayError),
    ):
        gw.chat([{"role": "user", "content": "hi"}])

    assert "Authorization" not in seen[0].headers


def test_embed_batches_and_preserves_order(tmp_path: Path) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        # Return the batch shuffled to prove `index` is what restores order.
        data = [
            {"index": i, "embedding": [float(ord(text[0]))]} for i, text in enumerate(body["input"])
        ]
        return httpx.Response(200, json={"data": list(reversed(data))})

    texts = ["a", "b", "c", "d", "e"]
    with make_gateway(tmp_path, handler) as gw:
        vectors = gw.embed(texts, batch_size=2)

    assert [b["input"] for b in bodies] == [["a", "b"], ["c", "d"], ["e"]]
    assert bodies[0]["model"] == "gemini-embedding-001"  # `auto` is rejected for embeddings
    assert vectors == [[float(ord(t))] for t in texts]


def test_embed_empty_input_makes_no_request(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not be called")

    with make_gateway(tmp_path, handler) as gw:
        assert gw.embed([]) == []


@pytest.mark.parametrize(
    "reply",
    [
        '```json\n{"ok": true}\n```',
        '```\n{"ok": true}\n```',
        'Sure! Here is the JSON you asked for:\n{"ok": true}\nHope that helps.',
        '{"ok": true}',
    ],
)
def test_chat_json_recovers_from_fences_and_prose(tmp_path: Path, reply: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_reply(reply))

    with make_gateway(tmp_path, handler) as gw:
        assert gw.chat_json([{"role": "user", "content": "go"}], schema_hint="{}") == {"ok": True}


def test_chat_json_parses_arrays(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_reply('Here you go:\n[{"id": "s1"}]'))

    with make_gateway(tmp_path, handler) as gw:
        assert gw.chat_json([{"role": "user", "content": "go"}], schema_hint="[]") == [{"id": "s1"}]


def test_chat_json_retries_invalid_json_then_succeeds(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        content = "not json at all" if len(calls) == 1 else '{"ok": true}'
        return httpx.Response(200, json=chat_reply(content))

    with make_gateway(tmp_path, handler) as gw:
        assert gw.chat_json([{"role": "user", "content": "go"}], schema_hint="{}") == {"ok": True}

    assert len(calls) == 2
    # The repair turn must show the model its own bad reply plus the parse error.
    repair = calls[1]["messages"]
    assert repair[-2]["role"] == "assistant"
    assert repair[-2]["content"] == "not json at all"
    assert "did not parse as JSON" in repair[-1]["content"]


def test_chat_json_raises_after_retries(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=chat_reply("still not json"))

    with make_gateway(tmp_path, handler) as gw, pytest.raises(GatewayError):
        gw.chat_json([{"role": "user", "content": "go"}], schema_hint="{}", retries=2)

    assert len(calls) == 2


def test_retries_on_500_then_succeeds(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(500, text="upstream exploded")
        return httpx.Response(200, json=chat_reply("recovered"))

    with make_gateway(tmp_path, handler) as gw:
        assert gw.chat([{"role": "user", "content": "hi"}]) == "recovered"

    assert len(calls) == 3


def test_retries_on_429(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json=chat_reply("ok"))

    with make_gateway(tmp_path, handler) as gw:
        assert gw.chat([{"role": "user", "content": "hi"}]) == "ok"

    assert len(calls) == 2


def test_no_retry_on_401(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="bad key")

    with make_gateway(tmp_path, handler) as gw, pytest.raises(GatewayError) as excinfo:
        gw.chat([{"role": "user", "content": "hi"}])

    assert len(calls) == 1
    assert excinfo.value.status_code == 401
    assert "bad key" in excinfo.value.body


def test_no_retry_on_400(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="auto not allowed for embeddings")

    with make_gateway(tmp_path, handler) as gw, pytest.raises(GatewayError):
        gw.embed(["x"])

    assert len(calls) == 1


def test_cache_hit_avoids_second_request(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=chat_reply("cached answer"))

    messages = [{"role": "user", "content": "expensive"}]
    with make_gateway(tmp_path, handler, use_cache=True) as gw:
        assert gw.chat(messages) == "cached answer"
        assert gw.chat(messages) == "cached answer"
        # A different payload must miss.
        gw.chat([{"role": "user", "content": "other"}])

    assert len(calls) == 2

    # A fresh Gateway reuses the on-disk cache — reruns cost nothing.
    with make_gateway(tmp_path, handler, use_cache=True) as gw2:
        assert gw2.chat(messages) == "cached answer"
    assert len(calls) == 2


def test_cache_disabled_repeats_request(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=chat_reply("fresh"))

    messages = [{"role": "user", "content": "same"}]
    with make_gateway(tmp_path, handler, use_cache=False) as gw:
        gw.chat(messages)
        gw.chat(messages)

    assert len(calls) == 2


def test_retries_on_transport_error(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, json=chat_reply("ok"))

    with make_gateway(tmp_path, handler) as gw:
        assert gw.chat([{"role": "user", "content": "hi"}]) == "ok"

    assert len(calls) == 2


def test_parse_json_rejects_scalars() -> None:
    with pytest.raises(ValueError):
        parse_json_reply("42")
    with pytest.raises(ValueError):
        parse_json_reply("   ")


def test_auto_model_retries_gateway_routing_failure(tmp_path: Path) -> None:
    """The gateway reports an unroutable upstream as a non-retriable 400.

    With `model: "auto"` that describes the gateway's routing table, not our
    request, and a retry lands on a different provider. Observed killing a
    727-segment enrichment run at 82%.
    """
    calls: list[int] = []
    body = (
        '{"error":{"message":"All providers failed: 404 The model '
        "`meta-llama/llama-4-scout-17b-16e-instruct` does not exist or you do "
        'not have access to it.","type":"input_nonretriable"}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(400, text=body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "recovered"}}]})

    gw = make_gateway(tmp_path, handler)
    assert gw.chat([{"role": "user", "content": "hi"}], model="auto") == "recovered"
    assert len(calls) == 2


def test_explicit_missing_model_is_not_retried(tmp_path: Path) -> None:
    """An explicit model that does not exist is a caller error, not routing."""
    calls: list[int] = []
    body = (
        '{"error":{"message":"The model `nope` does not exist or you do not have access to it."}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text=body)

    gw = make_gateway(tmp_path, handler)
    with pytest.raises(GatewayError):
        gw.chat([{"role": "user", "content": "hi"}], model="nope")
    assert len(calls) == 1


def test_embedding_model_switch_is_retried_then_rejected(tmp_path: Path) -> None:
    """The gateway falls back between embedding providers under load.

    Vectors from two models are not comparable, and on a real 727-segment run
    this produced 151 vectors at 3072 dims and 576 at 1024. Had the dimensions
    matched it would have corrupted retrieval silently.
    """
    served = iter(["gemini-embedding-001", "@cf/baai/bge-large-en-v1.5", "gemini-embedding-001"])

    def handler(request: httpx.Request) -> httpx.Response:
        model = next(served)
        n = len(json.loads(request.content)["input"])
        dim = 3 if model.startswith("gemini") else 2
        return httpx.Response(
            200,
            json={
                "model": model,
                "data": [{"index": i, "embedding": [0.1] * dim} for i in range(n)],
            },
        )

    gw = make_gateway(tmp_path, handler)
    # First call pins the space; the second is served by a fallback and must be
    # retried rather than accepted, and the retry succeeds.
    vectors = gw.embed(["a"], batch_size=1) + gw.embed(["b"], batch_size=1)
    assert [len(v) for v in vectors] == [3, 3]
    assert gw.embed_model_used == "gemini-embedding-001"


def test_wrong_model_response_is_not_cached(tmp_path: Path) -> None:
    """A rejected response must not poison the cache, or the retry would read
    the same bad answer back forever."""
    calls: list[str] = []
    replies = iter(["bad-model", "gemini-embedding-001"])

    def handler(request: httpx.Request) -> httpx.Response:
        model = next(replies)
        calls.append(model)
        return httpx.Response(
            200, json={"model": model, "data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )

    gw = make_gateway(tmp_path, handler, use_cache=True)
    gw._embed_model_used = "gemini-embedding-001"
    assert gw.embed(["x"], batch_size=1) == [[0.1, 0.2]]
    assert calls == ["bad-model", "gemini-embedding-001"], "the bad reply was retried"
