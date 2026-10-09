"""Turn a natural-language brief into a retrieval query and ordered beats.

"Start with education, escalate into career pressure, and finish with
marriage" is three things at once: a topic to retrieve on, a structure to
order by, and a duration hint. Splitting them out is what lets the
`progression` term mean anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mashup.gateway import Gateway, GatewayError

_SYSTEM = """You parse a creator's brief for assembling a mashup from their own archive.

Return JSON only:
{
  "query": "<a dense topical description used for semantic retrieval — expand \
the subject with words likely to appear in the actual transcript, not meta \
words like 'mashup', 'clip', 'set' or 'compilation'>",
  "beats": ["<ordered structural beat>", ...],
  "tone": "<one or two words, or empty>"
}

Beats are the ordered stages the creator asked for. If the brief names no
structure, return an empty beats list — do not invent one."""

# Fallback only. Ordered so that a phrase like "start with X, then Y, finish
# with Z" splits at every marker rather than only the first.
_BEAT_MARKERS = re.compile(
    r"\b(?:start(?:ing)? (?:with|on)|open(?:ing)? (?:with|on)|then|next|"
    r"escalat(?:e|ing) (?:into|to)|build(?:ing)? (?:into|to)|move (?:into|to)|"
    r"finish(?:ing)? (?:with|on)|end(?:ing)? (?:with|on)|clos(?:e|ing) (?:with|on))\b",
    re.IGNORECASE,
)
_STOPWORDS = re.compile(
    r"\b(?:a|an|the|about|minute|minutes|second|seconds|min|sec|set|mashup|"
    r"compilation|supercut|video|clip|clips|montage)\b",
    re.IGNORECASE,
)


@dataclass
class MashupRequest:
    prompt: str
    query: str
    beats: list[str] = field(default_factory=list)
    tone: str = ""


def _fallback_parse(prompt: str) -> MashupRequest:
    parts = [p.strip(" ,.;–—") for p in _BEAT_MARKERS.split(prompt)]
    beats = [p for p in parts[1:] if len(p) > 2] if len(parts) > 1 else []
    query = _STOPWORDS.sub(" ", prompt)
    query = re.sub(r"\d+\s*", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return MashupRequest(prompt=prompt, query=query or prompt, beats=beats)


def _reachable(gw: Gateway) -> bool:
    """Whether asking the model is worth attempting.

    A key means yes. Without one, the content-addressed cache from an earlier
    run can still answer this exact brief, which is much better than the regex
    path — but if there is no cache either, skip the doomed 401 round trip.
    With local embeddings that is what lets `build` run uncredentialled.
    """
    if getattr(gw.config, "gateway_api_key", ""):
        return True
    return (Path(gw.config.cache_dir) / "gateway").is_dir()


def parse_request(prompt: str, gw: Gateway | None = None) -> MashupRequest:
    if gw is None or not _reachable(gw):
        return _fallback_parse(prompt)
    try:
        data = gw.chat_json(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            schema_hint='{"query": str, "beats": [str], "tone": str}',
        )
    except (GatewayError, ValueError):
        # A brief is cheap to parse badly and expensive to fail on; the regex
        # path is always good enough to keep the pipeline moving.
        return _fallback_parse(prompt)

    # chat_json may hand back a bare array if the model wrapped the object.
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), {})
    if not isinstance(data, dict):
        return _fallback_parse(prompt)

    query = str(data.get("query") or "").strip()
    beats = [str(b).strip() for b in (data.get("beats") or []) if str(b).strip()]
    if not query:
        return _fallback_parse(prompt)
    return MashupRequest(
        prompt=prompt, query=query, beats=beats, tone=str(data.get("tone") or "").strip()
    )


def parse_duration(prompt: str, default: float) -> float:
    """Pull an explicit duration out of the brief, if there is one."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|\s)?\s*(minute|min|second|sec)s?\b", prompt, re.I)
    if not m:
        return default
    value = float(m.group(1))
    return value * 60 if m.group(2).lower().startswith("min") else value
