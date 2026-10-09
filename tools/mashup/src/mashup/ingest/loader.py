"""Archive -> (Source, cues).

This is the only place that decides where a source's subtitles come from, and
the only place that mints source ids. Ids are derived from the filename rather
than a hash so an EDL stays readable and a re-ingest of the same archive
produces stable references.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from mashup.ingest.media import MediaError, find_subtitle_for, probe_media
from mashup.ingest.subtitles import SubtitleError, parse_subtitles
from mashup.ingest.transcribe import TranscribeError, transcribe
from mashup.models import Cue, Source

MEDIA_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".mp3", ".m4a", ".wav"})
# Generated transcripts live in the workdir, never beside the creator's files.
TRANSCRIPT_DIR = "subtitles"


class IngestError(RuntimeError):
    """Raised when a media file cannot be turned into a Source."""


def ingest_source(
    media_path: Path,
    *,
    ordinal: int,
    workdir: Path,
    allow_transcribe: bool,
) -> tuple[Source, list[Cue]]:
    """Probe one media file, resolve its subtitles, and parse them."""
    media_path = media_path.expanduser().resolve()
    info = probe_media(media_path)
    source_id = slugify(media_path.stem)

    subtitle_path = find_subtitle_for(media_path)
    origin = "provided"
    if subtitle_path is None:
        cached = Path(workdir).expanduser().resolve() / TRANSCRIPT_DIR / f"{source_id}.srt"
        # A cached transcript is reusable even when transcription is disabled —
        # the expensive work already happened.
        if not cached.exists() and not allow_transcribe:
            raise IngestError(
                f"No subtitles for {media_path.name} and transcription is disabled. "
                f"Add a sibling .srt/.vtt or re-run with transcription enabled."
            )
        subtitle_path = transcribe(media_path, cached)
        origin = "transcribed"

    cues = parse_subtitles(subtitle_path)
    source = Source(
        id=source_id,
        path=str(media_path),
        title=humanise(media_path.stem),
        duration=info.duration,
        has_video=info.has_video,
        subtitle_path=str(subtitle_path),
        subtitle_origin=origin,
        ordinal=ordinal,
    )
    return source, cues


def ingest_archive(
    archive_dir: Path,
    *,
    workdir: Path,
    allow_transcribe: bool,
    start_ordinal: int = 0,
    skip_unreadable: bool = False,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> list[tuple[Source, list[Cue]]]:
    """Ingest every media file under `archive_dir`, in filename order.

    Ordinals follow that order: they are the planner's proxy for chronology, so
    the sort must be stable across runs and machines.
    """
    archive_dir = Path(archive_dir).expanduser().resolve()
    if not archive_dir.is_dir():
        raise IngestError(f"Archive directory not found: {archive_dir}")

    files = sorted(
        (p for p in archive_dir.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES),
        key=lambda p: str(p.relative_to(archive_dir)).lower(),
    )
    if not files:
        raise IngestError(f"No media files ({', '.join(sorted(MEDIA_SUFFIXES))}) in {archive_dir}")

    items: list[tuple[Source, list[Cue]]] = []
    errors: list[tuple[Path, Exception]] = []
    for i, path in enumerate(files):
        try:
            items.append(
                ingest_source(
                    path,
                    ordinal=start_ordinal + i,
                    workdir=workdir,
                    allow_transcribe=allow_transcribe,
                )
            )
        except (IngestError, MediaError, SubtitleError, TranscribeError) as exc:
            if not skip_unreadable:
                raise
            errors.append((path, exc))
            if on_error is not None:
                on_error(path, exc)

    if not items and errors:
        first_path, first_error = errors[0]
        raise IngestError(
            f"No readable media files in {archive_dir}; {len(errors)} failed. "
            f"First failure: {first_path.name}: {first_error}"
        ) from first_error
    return items


def slugify(stem: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    # Every Source needs an id even for a filename that is pure punctuation.
    return slug or "source"


def humanise(stem: str) -> str:
    title = re.sub(r"[\s_.-]+", " ", stem).strip()
    # Only title-case what is entirely lowercase, so "Ep 12: NASA Q&A" keeps its
    # capitalisation while "my-first-set" becomes readable.
    return title.title() if title.islower() else title
