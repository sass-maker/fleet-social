"""Getting structured output out of a model that will not promise it.

`response_format` is not portable: the gateway routes to whichever provider is
cheapest, and a local mlx model has no such parameter at all. Both backends
therefore ask for JSON in the prompt and parse defensively — fenced blocks,
prose wrappers, an outermost bracket span — with the caller free to retry.
"""

from __future__ import annotations

import json
import re
from typing import Any

JSONValue = dict[str, Any] | list[Any]

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def format_rules(schema_hint: str) -> str:
    return (
        "Respond with JSON only. No prose, no explanation, no markdown code fences.\n"
        f"Shape:\n{schema_hint}"
    )


def parse_json_reply(text: str) -> JSONValue:
    """Parse JSON out of a model reply that may be fenced or wrapped in prose."""
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty reply")

    candidates: list[str] = []
    fenced = _FENCE_RE.search(stripped)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(stripped)

    outermost = _outermost_span(stripped)
    if outermost is not None:
        candidates.append(outermost)

    error = "no JSON found"
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError as exc:
            error = str(exc)
            continue
        if isinstance(value, dict | list):
            return value
        error = f"expected object or array, got {type(value).__name__}"
    raise ValueError(error)


def _outermost_span(text: str) -> str | None:
    """Slice from the first `{`/`[` to its matching closing bracket."""
    starts = [(text.find(c), c) for c in "{[" if text.find(c) != -1]
    if not starts:
        return None
    start, opener = min(starts)
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        return None
    return text[start : end + 1]
