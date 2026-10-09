"""ffprobe wrapper.

The planner needs to know a source's true duration (subtitle files routinely
end early) and whether there is a video track at all, since audio-only sources
can only be placed in audio mashups.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FFPROBE_ARGS = ("-v", "quiet", "-print_format", "json", "-show_format", "-show_streams")
SUBTITLE_SUFFIXES = (".srt", ".vtt")


class MediaError(RuntimeError):
    """Raised when a media file cannot be probed."""


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    fps: float | None = None


def probe_media(path: Path) -> MediaInfo:
    """Read container/stream metadata via ffprobe."""
    if not path.is_file():
        raise MediaError(f"Media file not found: {path}")
    if shutil.which("ffprobe") is None:
        raise MediaError("ffprobe not found on PATH. Install FFmpeg (`brew install ffmpeg`).")

    proc = subprocess.run(
        ["ffprobe", *FFPROBE_ARGS, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise MediaError(f"ffprobe could not read {path}: {proc.stderr.strip() or 'no output'}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned unparseable JSON for {path}") from exc

    streams = data.get("streams") or []
    # An mp3 cover image is an "attached_pic" video stream — counting it would
    # make an audio-only podcast look renderable as video.
    video = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    audio = [s for s in streams if s.get("codec_type") == "audio"]

    duration = _duration(data.get("format") or {}, streams)
    if duration <= 0:
        raise MediaError(f"ffprobe reported no duration for {path}")

    first = video[0] if video else {}
    return MediaInfo(
        path=path,
        duration=duration,
        has_video=bool(video),
        has_audio=bool(audio),
        width=_as_int(first.get("width")),
        height=_as_int(first.get("height")),
        fps=_fps(first.get("r_frame_rate")),
    )


def find_subtitle_for(media_path: Path) -> Path | None:
    """Sibling subtitle with the same stem. SRT wins — it is the format the
    rest of the pipeline round-trips through."""
    for suffix in SUBTITLE_SUFFIXES:
        candidate = media_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def _duration(fmt: dict, streams: list[dict]) -> float:
    # Container duration is missing for some MKV/streamed files; fall back to
    # the longest stream rather than failing the whole ingest.
    values = [fmt.get("duration"), *(s.get("duration") for s in streams)]
    best = 0.0
    for value in values:
        try:
            best = max(best, float(value))
        except (TypeError, ValueError):
            continue
    return best


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fps(rate: object) -> float | None:
    if not isinstance(rate, str) or "/" not in rate:
        return None
    num, _, den = rate.partition("/")
    try:
        denominator = float(den)
        return float(num) / denominator if denominator else None
    except ValueError:
        return None
