"""Cut-point detection and snapping.

Planner boundaries come from subtitle timings, which are approximate: cutting
exactly on them clips the first or last syllable of a line. This module finds a
natural edge near the requested cut — a silence, or a gap between subtitle cues
— and moves the cut there, always outward, so speech is never truncated.

`snap_boundaries` is deliberately pure (no ffmpeg, no I/O) so the interesting
logic is unit-testable; `detect_silences` is the only part that shells out.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from mashup.models import Cue

# Never emit a cut shorter than this — sub-half-second clips are artefacts of a
# bad snap, not real material, and they break xfade offsets downstream.
MIN_CLIP_DURATION = 0.5

# silencedetect logs `silence_start: 12.34` / `silence_end: 13.9 | silence_duration: 1.5`.
# `silence_duration` deliberately does not match, so pairing stays simple.
_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)")


class ToolError(RuntimeError):
    """An ffmpeg/ffprobe invocation failed. Carries the tail of stderr."""


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def ffprobe_bin() -> str:
    return shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def run_tool(cmd: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run an ffmpeg-family command (always a list, never a shell string).

    Callers read `.stderr` for ffmpeg's analysis output (silencedetect,
    loudnorm) and `.stdout` for ffprobe's JSON.
    """
    proc = subprocess.run(
        [str(c) for c in cmd],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        check=False,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-15:])
        raise ToolError(f"{Path(cmd[0]).name} failed ({proc.returncode}):\n{tail}")
    return proc


def parse_silencedetect(stderr: str) -> list[tuple[float, float]]:
    """Pull (start, end) silence spans out of ffmpeg's stderr.

    Split out from `detect_silences` so the parser can be tested against
    captured sample output without running ffmpeg. A trailing `silence_start`
    with no matching end (the file fades out into silence) is dropped: without
    the container duration we cannot close the span honestly.
    """
    spans: list[tuple[float, float]] = []
    pending: float | None = None
    for kind, value in _SILENCE_RE.findall(stderr):
        # Clamp: ffmpeg occasionally reports a marginally negative start.
        seconds = max(0.0, float(value))
        if kind == "start":
            pending = seconds
        elif pending is not None:
            if seconds > pending:
                spans.append((pending, seconds))
            pending = None
    return spans


def _cache_path(media: Path, cache_dir: Path, noise_db: float, min_dur: float) -> Path:
    # Key on file identity *and* detection parameters, so touching the media or
    # retuning the threshold invalidates the sidecar.
    stat = media.stat()
    raw = f"{media.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{noise_db}|{min_dur}"
    return cache_dir / f"silences-{hashlib.sha256(raw.encode()).hexdigest()[:20]}.json"


def detect_silences(
    media: Path,
    *,
    noise_db: float = -32.0,
    min_dur: float = 0.28,
    cache_dir: Path | None = None,
) -> list[tuple[float, float]]:
    """Scan `media` for silences via ffmpeg's silencedetect filter.

    This decodes the entire file, so results are cached to a JSON sidecar under
    `cache_dir` when one is supplied.
    """
    media = Path(media)
    if not media.exists():
        raise FileNotFoundError(f"media not found: {media}")

    sidecar = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        sidecar = _cache_path(media, cache_dir, noise_db, min_dur)
        if sidecar.exists():
            try:
                return [(float(a), float(b)) for a, b in json.loads(sidecar.read_text())]
            except (ValueError, TypeError, json.JSONDecodeError):
                sidecar.unlink(missing_ok=True)  # corrupt sidecar: re-scan

    proc = run_tool(
        [
            ffmpeg_bin(),
            "-nostdin",
            "-hide_banner",
            "-i",
            str(media),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_dur}",
            "-vn",
            "-f",
            "null",
            "-",
        ]
    )
    spans = parse_silencedetect(proc.stderr)
    if sidecar is not None:
        sidecar.write_text(json.dumps([list(s) for s in spans]))
    return spans


def _cue_gap_points(cues: Sequence[Cue]) -> list[float]:
    """Midpoints of the pauses between consecutive subtitle lines."""
    ordered = sorted(cues, key=lambda c: c.start)
    return [
        (prev.end + nxt.start) / 2
        for prev, nxt in zip(ordered, ordered[1:], strict=False)
        if nxt.start > prev.end
    ]


def _pick(
    target: float, candidates: Sequence[float], window: float, *, outward_is_lower: bool
) -> float | None:
    """Nearest candidate inside `window`, with a hard preference for the safe side.

    Any candidate on the outward side beats every inward one: widening the cut
    only adds a beat of air, whereas narrowing it eats speech.
    """
    near = [c for c in candidates if abs(c - target) <= window]
    if not near:
        return None
    outward = [c for c in near if (c <= target if outward_is_lower else c >= target)]
    return min(outward or near, key=lambda c: abs(c - target))


def _snap_one(target: float, silences, cues, window: float, *, outward_is_lower: bool) -> float:
    # Priority order: silence midpoint, then subtitle gap, then leave it alone.
    mids = [(a + b) / 2 for a, b in silences]
    return (
        _pick(target, mids, window, outward_is_lower=outward_is_lower)
        or _pick(target, _cue_gap_points(cues), window, outward_is_lower=outward_is_lower)
        or target
    )


def snap_boundaries(
    start: float,
    end: float,
    *,
    silences: Sequence[tuple[float, float]] = (),
    cues: Sequence[Cue] = (),
    window: float = 1.2,
) -> tuple[float, float]:
    """Move a planned cut to the most natural edge within `window` seconds.

    Starts drift earlier and ends drift later, so the clip grows rather than
    shrinks. The result never inverts and is never shorter than
    `MIN_CLIP_DURATION`.
    """
    snapped_start = _snap_one(start, silences, cues, window, outward_is_lower=True)
    snapped_end = _snap_one(end, silences, cues, window, outward_is_lower=False)
    snapped_start = max(0.0, snapped_start)

    if snapped_end - snapped_start < MIN_CLIP_DURATION:
        # A snap that collapsed the clip is worse than no snap: fall back to the
        # union of the snapped and original spans, which can only be wider.
        snapped_start = max(0.0, min(snapped_start, start))
        snapped_end = max(snapped_end, end)
    if snapped_end - snapped_start < MIN_CLIP_DURATION:
        # The planner itself handed us something too short; pad the tail.
        snapped_end = snapped_start + MIN_CLIP_DURATION
    return snapped_start, snapped_end
