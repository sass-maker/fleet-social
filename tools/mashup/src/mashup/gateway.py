"""Access to the fleet free-ai gateway (OpenAI-compatible).

Everything that costs money or network time in this project goes through
`Gateway`. Two properties matter more than anything else here:

1. *Defensive JSON parsing.* The gateway routes to whichever model is cheapest
   right now, so replies vary in how well they respect "return JSON". The
   pipeline must not die because a model wrapped its answer in prose.
2. *An on-disk cache.* Enrichment is re-run constantly while tuning prompts
   downstream of it; a cached rerun must cost nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from mashup.config import Config
from mashup.jsonreply import JSONValue, format_rules, parse_json_reply

# Status codes worth retrying: rate limits and server-side faults. 400/401/403
# are our fault and will fail identically forever.
_RETRYABLE_STATUS = {408, 409, 429}

# The gateway reports an unroutable upstream as a 400 tagged
# `input_nonretriable`, e.g. "All providers failed: 404 The model
# `meta-llama/...` does not exist". With `model: "auto"` that is a statement
# about the gateway's routing table, not about our request — a retry lands on
# a different provider. Observed killing a 727-segment enrichment run at 82%.
_ROUTING_FAILURE = re.compile(
    r"all providers failed|does not exist or you do not have access", re.IGNORECASE
)


def _is_routing_failure(status: int, text: str, body: dict[str, Any]) -> bool:
    if status != 400 or not _ROUTING_FAILURE.search(text):
        return False
    # Only retry when we let the gateway choose. An explicit model that does
    # not exist is a real caller error and must surface immediately.
    return str(body.get("model", "")).lower() == "auto"


_BODY_EXCERPT = 500


class GatewayError(RuntimeError):
    """A gateway call failed in a way the caller cannot paper over."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = "") -> None:
        self.status_code = status_code
        self.body = body[:_BODY_EXCERPT]
        detail = f" [status={status_code}]" if status_code is not None else ""
        detail += f" body={self.body!r}" if self.body else ""
        super().__init__(f"{message}{detail}")


class _TransientGatewayError(GatewayError):
    """Retryable variant — kept private so callers only ever match GatewayError."""


class Gateway:
    """Thin OpenAI-compatible client with retries and a content-addressed cache."""

    def __init__(
        self,
        config: Config,
        *,
        use_cache: bool = True,
        timeout: float = 120.0,
        retry_attempts: int = 4,
        retry_wait: float = 1.0,
        retry_max_wait: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.use_cache = use_cache
        self._retry_attempts = retry_attempts
        self._retry_wait = retry_wait
        self._retry_max_wait = retry_max_wait
        self._cache_dir = Path(config.cache_dir) / "gateway"
        # Which embedding model the gateway actually served, so a mid-run
        # provider fallback cannot silently mix vector spaces.
        self._embed_model_used: str | None = None
        headers = {"Content-Type": "application/json"}
        if config.gateway_api_key:
            headers["Authorization"] = f"Bearer {config.gateway_api_key}"
        self._client = httpx.Client(
            base_url=config.gateway_url,
            timeout=timeout,
            transport=transport,
            headers=headers,
        )

    # ---------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Gateway:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------- public

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_object: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.config.chat_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        data = self._post("/v1/chat/completions", payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError(f"malformed chat response: {exc}", body=json.dumps(data)) from exc

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        schema_hint: str,
        model: str | None = None,
        retries: int = 3,
    ) -> JSONValue:
        """Ask for JSON and keep asking until it parses.

        `response_format` is deliberately not sent: the gateway may route to a
        model that rejects it. The parser below plus a repair round-trip is the
        portable way to get structured output from an unknown model.
        """
        # Format rules go last so they are the freshest instruction, and in
        # their own message so the caller's prompt stays untouched.
        convo = [*messages, {"role": "system", "content": format_rules(schema_hint)}]
        last_error = ""
        for attempt in range(max(1, retries)):
            reply = self.chat(convo, model=model, temperature=0.0 if attempt else 0.2)
            try:
                return parse_json_reply(reply)
            except ValueError as exc:
                last_error = str(exc)
                convo = [
                    *convo,
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": (
                            f"That did not parse as JSON: {last_error}\n"
                            "Reply again with the JSON value only — no prose, no code fences."
                        ),
                    },
                ]
        raise GatewayError(
            f"model never returned valid JSON after {retries} attempts: {last_error}"
        )

    def chat_json_many(
        self,
        conversations: Sequence[list[dict[str, Any]]],
        *,
        schema_hint: str,
        concurrency: int = 4,
    ) -> list[JSONValue | None]:
        """Answer several prompts concurrently; `None` marks one that failed.

        The local backend satisfies the same contract with a single batched
        forward pass. Keeping the shape identical is what lets enrichment stay
        ignorant of which one it is talking to.
        """
        conversations = list(conversations)
        if not conversations:
            return []
        out: list[JSONValue | None] = [None] * len(conversations)

        def one(index: int) -> tuple[int, JSONValue | None]:
            try:
                return index, self.chat_json(conversations[index], schema_hint=schema_hint)
            except GatewayError:
                # One unroutable prompt must not discard the rest.
                return index, None

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            for index, value in pool.map(one, range(len(conversations))):
                out[index] = value
        return out

    @property
    def name(self) -> str:
        return f"gateway:{self.config.chat_model}"

    def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        batch_size: int = 16,
    ) -> list[list[float]]:
        """Embed `texts`, returning vectors in the same order as the input."""
        if not texts:
            return []
        out: list[list[float]] = []
        embed_model = model or self.config.embed_model

        def same_space(data: dict[str, Any]) -> None:
            """Reject a response served by a different embedding model.

            Raised as transient so the retry loop re-asks: the gateway falls
            back under load, and a moment later the requested model is
            usually available again.
            """
            served = str(data.get("model") or embed_model)
            expected = self._embed_model_used or embed_model
            if served != expected:
                raise _TransientGatewayError(
                    f"embeddings served by {served!r}, expected {expected!r}"
                )

        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            data = self._post(
                "/v1/embeddings", {"model": embed_model, "input": chunk}, validate=same_space
            )
            try:
                items = data["data"]
                # Providers are allowed to return the batch out of order; `index`
                # is the only authoritative mapping back to the input.
                ordered = sorted(items, key=lambda d: d.get("index", 0))
                vectors = [list(item["embedding"]) for item in ordered]
            except (KeyError, TypeError) as exc:
                raise GatewayError(
                    f"malformed embeddings response: {exc}", body=json.dumps(data)
                ) from exc
            if len(vectors) != len(chunk):
                raise GatewayError(f"expected {len(chunk)} embeddings, got {len(vectors)}")

            # The gateway falls back across embedding providers, and a fallback
            # returns vectors in a completely different space. Observed on a
            # 727-segment run: 151 vectors at 3072 dims from gemini and 576 at
            # 1024 from a voyage fallback. Cosine similarity across two spaces
            # is meaningless, and had the dimensions happened to match it would
            # have corrupted retrieval silently instead of raising.
            served = str(data.get("model") or embed_model)
            if self._embed_model_used is None:
                self._embed_model_used = served
            elif served != self._embed_model_used:
                raise GatewayError(
                    f"gateway switched embedding model mid-run: "
                    f"{self._embed_model_used!r} -> {served!r}. Vectors from "
                    f"different models are not comparable; re-embed the corpus."
                )
            out.extend(vectors)
        return out

    @property
    def embed_model_used(self) -> str | None:
        """The model that actually served embeddings, as reported by the gateway."""
        return self._embed_model_used

    # ------------------------------------------------------------------ internal

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """POST with retry and caching.

        `validate` runs inside the retry loop and before the response is
        cached, so a response the caller considers unusable is retried rather
        than persisted. A cached response is validated too — an entry written
        before the rule existed must not be trusted forever.
        """
        body = dict(payload)
        key = _cache_key(path, str(body.get("model", "")), body)
        cached = self._cache_read(key)
        if cached is not None:
            if validate is None:
                return cached
            try:
                validate(cached)
                return cached
            except GatewayError:
                self._cache_delete(key)
        data = self._post_with_retry(path, body, validate)
        self._cache_write(key, data)
        return data

    def _post_with_retry(
        self,
        path: str,
        body: dict[str, Any],
        validate: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        retrying = Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(multiplier=self._retry_wait, max=self._retry_max_wait),
            retry=retry_if_exception_type((_TransientGatewayError, httpx.TransportError)),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                data = self._send(path, body)
                if validate is not None:
                    validate(data)
                return data
        raise GatewayError("retry loop exhausted without a result")  # pragma: no cover

    def _send(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(path, json=body)
        if response.status_code >= 400:
            text = response.text
            transient = (
                response.status_code >= 500
                or response.status_code in _RETRYABLE_STATUS
                or _is_routing_failure(response.status_code, text, body)
            )
            cls = _TransientGatewayError if transient else GatewayError
            raise cls(f"POST {path} failed", status_code=response.status_code, body=text)
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayError(f"POST {path} returned non-JSON", body=response.text) from exc

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _cache_read(self, key: str) -> dict[str, Any] | None:
        if not self.use_cache:
            return None
        path = self._cache_path(key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt or missing entry is just a miss.
            return None

    def _cache_delete(self, key: str) -> None:
        """Drop a cached response that turned out to be unusable."""
        with contextlib.suppress(OSError):
            (self._cache_dir / f"{key}.json").unlink(missing_ok=True)

    def _cache_write(self, key: str, data: dict[str, Any]) -> None:
        if not self.use_cache:
            return
        path = self._cache_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash mid-write cannot leave a truncated
            # entry that a later run would read as a hit.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            # The cache is an optimisation; never fail a call over it.
            pass


def _cache_key(endpoint: str, model: str, payload: dict[str, Any]) -> str:
    blob = json.dumps([endpoint, model, payload], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
