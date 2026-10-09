"""SRT / WebVTT parsing.

Creator archives ship wildly inconsistent subtitles: hand-authored SRT with
karaoke markup, YouTube VTT with rolling inline timestamps, files with no hour
field, files with speaker names baked into the text. Everything downstream
(segmentation, embedding, the LLM extractor) assumes clean plain text on a
seconds timeline, so all of that normalisation happens here and nowhere else.

Parsing is deliberately forgiving — one malformed block in a 90-minute episode
must not cost the whole episode. Only a file that yields nothing is an error.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from mashup.models import Cue

# Longest string still plausibly a speaker label in `NAME: text`. Beyond this
# the colon is almost certainly punctuation inside a sentence.
MAX_SPEAKER_LEN = 30

_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})$")
_TIMING = re.compile(r"^(?P<start>[\d:.,]+)\s*-->\s*(?P<end>[\d:.,]+)(?P<settings>.*)$")
_INDEX = re.compile(r"^\d+$")

# `{\an8}`-style ASS/SSA overrides leak into SRT from muxed soft subs.
_ASS_OVERRIDE = re.compile(r"\{\\[^}]*\}")
# VTT karaoke cues interleave `<00:00:01.000>` between words.
_INLINE_TIMESTAMP = re.compile(r"<\d{1,3}:\d{2}(?::\d{2})?[.,]\d{1,3}>")
# Whisper special tokens. WhisperKit writes these straight into its SRT:
# `<|startoftranscript|><|en|><|transcribe|><|0.00|> text <|4.00|>`. They are
# not markup — `_TAG` requires a letter after `<` and skips them — so without
# this they reach the segment text, the embeddings, and the enrichment prompt.
_WHISPER_TOKEN = re.compile(r"<\|[^|>]*\|>")
# Any real tag: <i>, </b>, <font color="#fff">, <c.loud>, <v Name>.
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_VOICE = re.compile(r"<v(?:\.[^\s>]+)*[\s.]+([^>]+)>")
_SPEAKER_PREFIX = re.compile(rf"^(?P<name>[^:]{{1,{MAX_SPEAKER_LEN}}}?)\s*:\s+(?P<rest>\S.*)$")
# A name word: initial capital (John, JOHN, O'Brien, Dr.) or a bare number.
_NAME_WORD = re.compile(r"^(?:[A-Z][\w.'’-]*|\d+)$")

# `X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000` — HLS-extracted VTT states
# its offset here. We parse it for completeness but never apply it: mashup cuts
# against the media file's own timeline, which the LOCAL side already matches.
_TIMESTAMP_MAP = re.compile(r"X-TIMESTAMP-MAP\s*=\s*(?P<body>\S+)", re.IGNORECASE)


class SubtitleError(ValueError):
    """Raised when a subtitle file yields no usable cues."""


def parse_subtitles(path: Path) -> list[Cue]:
    """Parse an .srt or .vtt file into normalised cues."""
    suffix = path.suffix.lower()
    if suffix == ".srt":
        parser = parse_srt
    elif suffix in {".vtt", ".webvtt"}:
        parser = parse_vtt
    else:
        raise SubtitleError(f"Unsupported subtitle format {suffix!r}: {path}")
    # utf-8-sig drops the BOM that Windows-authored SRT files carry; a stray
    # replacement char is better than losing an entire episode to one bad byte.
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    cues = parser(text)
    if not cues:
        raise SubtitleError(f"No usable cues in {path}")
    return cues


def parse_srt(text: str) -> list[Cue]:
    raw: list[tuple[float, float, str]] = []
    for lines in _blocks(text):
        # The index line is optional and unreliable; we renumber anyway.
        if _INDEX.match(lines[0]) and len(lines) > 1 and "-->" in lines[1]:
            lines = lines[1:]
        parsed = _parse_block(lines)
        if parsed is not None:
            raw.append(parsed)
    return _finalise(raw)


def parse_vtt(text: str) -> list[Cue]:
    raw: list[tuple[float, float, str]] = []
    for lines in _blocks(text):
        head = lines[0]
        if head.startswith("WEBVTT"):
            parse_timestamp_map("\n".join(lines))
            continue
        # NOTE/STYLE/REGION blocks carry no timing and must never become cues.
        if re.match(r"^(NOTE|STYLE|REGION)\b", head):
            continue
        # An optional cue identifier precedes the timing line.
        if "-->" not in head and len(lines) > 1 and "-->" in lines[1]:
            lines = lines[1:]
        parsed = _parse_block(lines)
        if parsed is not None:
            raw.append(parsed)
    # YouTube auto-captions use a rolling two-line window: each cue repeats
    # the previous line and adds the next one. Inline karaoke timestamps are a
    # reliable marker for that format. Collapse only those files so ordinary
    # VTT cues are never changed merely because adjacent speakers repeat text.
    return _finalise(raw, dedupe_rolling=_INLINE_TIMESTAMP.search(text) is not None)


def parse_timestamp_map(header: str) -> dict[str, str]:
    """Extract `X-TIMESTAMP-MAP` fields from a WEBVTT header block."""
    match = _TIMESTAMP_MAP.search(header)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for part in match.group("body").split(","):
        key, _, value = part.partition(":")
        if value:
            fields[key.strip().upper()] = value.strip()
    return fields


def _blocks(text: str) -> list[list[str]]:
    """Split into blank-line-separated blocks of non-empty lines."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _parse_block(lines: list[str]) -> tuple[float, float, str] | None:
    """Turn `[timing, *text]` into `(start, end, raw_text)`, or None if unusable."""
    if not lines:
        return None
    timing = _TIMING.match(lines[0])
    if not timing:
        return None
    # VTT cue settings (align:, position:, line:, size:) land in the `settings`
    # group and are dropped — they are presentation-only.
    start = _to_seconds(timing.group("start"))
    end = _to_seconds(timing.group("end"))
    if start is None or end is None or end < start:
        return None
    return start, end, "\n".join(lines[1:])


def _to_seconds(stamp: str) -> float | None:
    match = _TIMESTAMP.match(stamp.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = match.groups()
    # `MM:SS.mmm` (no hours) is legal VTT and common in hand-written SRT.
    return (
        int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000
    )


def _clean(raw: str) -> tuple[str, str | None]:
    """Strip markup and lift the speaker out of the text."""
    text = _ASS_OVERRIDE.sub("", raw)
    voice = _VOICE.search(text)
    # `<v Name>` may carry classes and trailing annotations: `<v.loud Bob Loud>`.
    speaker = voice.group(1).strip() or None if voice else None
    text = _INLINE_TIMESTAMP.sub("", text)
    text = _WHISPER_TOKEN.sub("", text)
    text = _TAG.sub("", text)
    # Unescape last so `&lt;i&gt;` survives as literal text rather than a tag.
    text = html.unescape(text)
    # Line breaks inside a cue are layout, not meaning; downstream joins cue
    # text into paragraphs anyway.
    text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    text = re.sub(r"\s+", " ", text).strip()
    if speaker is None:
        speaker, text = _split_speaker_prefix(text)
    return text, speaker


def _split_speaker_prefix(text: str) -> tuple[str | None, str]:
    match = _SPEAKER_PREFIX.match(text)
    if not match:
        return None, text
    name = match.group("name").strip()
    if not _looks_like_speaker(name):
        return None, text
    return name, match.group("rest").strip()


def _looks_like_speaker(name: str) -> bool:
    words = name.replace("-", " ").split()
    # Real labels are short: "JOHN", "Dr. Ana", "SPEAKER 2" — not a clause.
    if not words or len(words) > 4:
        return False
    return all(_NAME_WORD.match(word) for word in words)


def _rolling_delta(text: str, previous: str) -> str:
    """Return only words newly introduced by a rolling caption window."""
    current_words = text.split()
    previous_words = previous.split()
    if not current_words or not previous_words:
        return text
    if current_words == previous_words:
        return ""

    # A rolling cue begins with the tail of the previous cue. This format also
    # emits one-word finalisation windows, so a one-token overlap is meaningful;
    # ordinary VTT never reaches this branch.
    for width in range(min(len(current_words), len(previous_words)), 0, -1):
        if previous_words[-width:] == current_words[:width]:
            return " ".join(current_words[width:])
    return text


def _finalise(raw: list[tuple[float, float, str]], *, dedupe_rolling: bool = False) -> list[Cue]:
    """Clean text, drop empties, renumber. Indices must be dense because
    segments reference cue ranges by position."""
    cues: list[Cue] = []
    previous_text = ""
    for start, end, body in raw:
        text, speaker = _clean(body)
        if dedupe_rolling:
            full_text = text
            text = _rolling_delta(full_text, previous_text)
            previous_text = full_text
        if not text:
            continue
        cues.append(Cue(index=len(cues), start=start, end=end, text=text, speaker=speaker))
    return cues
