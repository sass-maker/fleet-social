"""Ingest stage: archive files -> `Source` records and normalised `Cue` lists."""

from __future__ import annotations

from mashup.ingest.loader import ingest_archive, ingest_source
from mashup.ingest.media import MediaInfo, find_subtitle_for, probe_media
from mashup.ingest.subtitles import parse_subtitles
from mashup.ingest.transcribe import transcribe

__all__ = [
    "MediaInfo",
    "find_subtitle_for",
    "ingest_archive",
    "ingest_source",
    "parse_subtitles",
    "probe_media",
    "transcribe",
]
