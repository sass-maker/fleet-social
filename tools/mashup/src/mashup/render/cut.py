"""Render an EDL to a single playable MP4.

Strategy: extract every clip to its own intermediate with identical codecs,
resolution, frame rate, sample rate and timebase, then concatenate. Extraction
re-encodes on purpose — stream copy can only cut on keyframes, and a mashup cuts
at arbitrary conversational boundaries.

The real problem being solved is that clips come from different episodes
recorded years apart, so per-clip `loudnorm` (EBU R128) is applied during
extraction rather than once over the finished timeline; normalising the
concatenation would preserve the level jumps between clips.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mashup.models import EDL, Clip
from mashup.render.boundaries import ToolError, ffmpeg_bin, ffprobe_bin, run_tool
from mashup.render.provenance import (
    write_label_card,
    write_visual_credit_card,
    write_watermark_card,
)

Progress = Callable[[str], None]
RenderProfile = Literal["source", "social"]

DEFAULT_SIZE = (1280, 720)
SOCIAL_SIZE = (1080, 1920)
DEFAULT_FPS = 30.0
# Neutral card behind audio-only clips: dark enough not to flash between video
# clips, not pure black so the render reads as intentional.
CARD_COLOUR = "0x101014"
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
_AFORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
# Bump to invalidate every cached intermediate after a recipe change.
_RECIPE = "v6"
_SRT_LINE = 84

_BASE = ("-y", "-nostdin", "-hide_banner", "-loglevel", "error")
# Every intermediate *and* the final mux encode identically: the concat demuxer
# only joins streams whose codec parameters and timebase already match.
_VIDEO_ENC: dict[str, str] = {
    "-c:v": "libx264",
    "-preset": "veryfast",
    "-crf": "20",
    "-pix_fmt": "yuv420p",
    "-video_track_timescale": "90000",
}
_AUDIO_ENC: dict[str, str] = {"-c:a": "aac", "-b:a": "192k", "-ar": "48000", "-ac": "2"}


def _flags(*groups: dict[str, str]) -> list[str]:
    return [token for group in groups for pair in group.items() for token in pair]


class MissingSourceError(FileNotFoundError):
    """One or more `Clip.source_path` entries do not exist on disk."""


@dataclass(frozen=True)
class MediaInfo:
    has_video: bool
    has_audio: bool
    width: int
    height: int
    fps: float
    duration: float


@functools.lru_cache(maxsize=1)
def has_subtitles_filter() -> bool:
    """Whether this ffmpeg can burn subtitles (needs a libass-enabled build).

    Homebrew's default bottle ships without libass, so `subtitles="burn"` has to
    fail with an explanation rather than an opaque filtergraph parse error.
    """
    proc = run_tool([ffmpeg_bin(), "-hide_banner", "-filters"])
    return re.search(r"^\s*\S+\s+subtitles\s", proc.stdout, re.MULTILINE) is not None


def _fraction(value: str | None) -> float:
    if not value or "/" not in value:
        return 0.0
    num, den = value.split("/", 1)
    return float(num) / float(den) if float(den) else 0.0


def probe(path: Path) -> MediaInfo:
    proc = run_tool(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
    )
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = 0.0
    if video:
        fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    return MediaInfo(
        has_video=video is not None,
        has_audio=audio is not None,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=fps or DEFAULT_FPS,
        duration=float(data.get("format", {}).get("duration") or 0.0),
    )


def _target_format(
    edl: EDL,
    infos: dict[str, MediaInfo],
    profile: RenderProfile = "source",
) -> tuple[tuple[int, int], float]:
    """Choose the modal source format, preferring maximum quality on a tie."""
    formats: Counter[tuple[int, int, float]] = Counter()
    seen_sources: set[str] = set()
    for clip in edl.clips:
        if clip.source_path in seen_sources:
            continue
        seen_sources.add(clip.source_path)
        info = infos[clip.source_path]
        if info.has_video and info.width and info.height:
            # Even dimensions are required by yuv420p / libx264.
            width = info.width - info.width % 2
            height = info.height - info.height % 2
            formats[(width, height, round(info.fps, 3))] += 1

    if profile == "social":
        fps = (
            max(formats, key=lambda format_: (formats[format_], format_[2]))[2]
            if formats
            else DEFAULT_FPS
        )
        return SOCIAL_SIZE, fps
    if formats:
        width, height, fps = max(
            formats,
            key=lambda format_: (
                formats[format_],
                format_[0] * format_[1],
                format_[2],
                format_[0],
                format_[1],
            ),
        )
        return (width, height), fps
    return DEFAULT_SIZE, DEFAULT_FPS


def _clip_key(
    clip: Clip,
    size: tuple[int, int],
    fps: float,
    normalise: bool,
    source_label: bool,
    watermark: bool,
    watermark_text: str,
    profile: RenderProfile,
) -> str:
    """Identity of a rendered intermediate: the clip fields that affect pixels.

    `transition` and crossfade length are excluded — they are applied at concat
    time, so changing them still reuses every intermediate.
    """
    src = Path(clip.source_path)
    stat = src.stat()
    visual_identity: list[object] = []
    for visual in clip.visuals:
        visual_source = Path(visual.source_path)
        visual_stat = visual_source.stat()
        visual_identity.extend(
            (
                visual.source_path,
                visual_stat.st_size,
                visual_stat.st_mtime_ns,
                visual.mode,
                round(visual.source_time, 4),
                round(visual.start, 4),
                round(visual.end, 4),
                visual.source_title,
                visual.source_url,
            )
        )
    raw = "|".join(
        str(x)
        for x in (
            _RECIPE,
            clip.source_path,
            stat.st_size,
            stat.st_mtime_ns,
            round(clip.render_start, 4),
            round(clip.render_end, 4),
            size[0],
            size[1],
            round(fps, 4),
            normalise,
            source_label,
            clip.source_title if source_label else "",
            clip.source_id if source_label else "",
            round(clip.start, 4) if source_label else "",
            round(clip.end, 4) if source_label else "",
            watermark,
            watermark_text if watermark else "",
            profile,
            *visual_identity,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _extract_cmd(
    clip: Clip,
    info: MediaInfo,
    dst: Path,
    *,
    size: tuple[int, int],
    fps: float,
    normalise: bool,
    label: Path | None,
    watermark: Path | None,
    visual_credits: Sequence[Path],
) -> list[str]:
    width, height = size
    duration = clip.render_duration
    # -ss before -i seeks fast; accuracy is guaranteed because we re-encode.
    cmd = [ffmpeg_bin(), *_BASE]
    cmd += ["-ss", f"{clip.render_start:.4f}", "-t", f"{duration:.4f}", "-i", clip.source_path]

    if info.has_video:
        video_map, next_input = "0:v:0", 1
    else:
        # Audio-only source: lay it over a static card so concat stays uniform
        # and the output remains a normal, playable MP4.
        cmd += ["-f", "lavfi", "-i", f"color=c={CARD_COLOUR}:s={width}x{height}:r={fps}"]
        video_map, next_input = "1:v:0", 2

    if info.has_audio:
        audio_map = "0:a:0"
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio_map = f"{next_input}:a:0"
        next_input += 1

    vfilter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    achain = ([LOUDNORM] if normalise else []) + [_AFORMAT]

    if clip.visuals or label is not None or watermark is not None:
        graph = [f"[{video_map}]{vfilter}[base]"]
        current = "base"
        margin = max(12, width // 40)
        frame_duration = max(1 / fps, 0.04)
        image_suffixes = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
        for visual_index, (visual, credit) in enumerate(
            zip(clip.visuals, visual_credits, strict=True)
        ):
            visual_path = Path(visual.source_path)
            if visual_path.suffix.lower() in image_suffixes:
                cmd += ["-loop", "1", "-framerate", f"{fps:g}", "-i", str(visual_path)]
                still_filter = f"trim=duration={duration:.4f},setpts=PTS-STARTPTS"
            else:
                cmd += ["-ss", f"{visual.source_time:.4f}", "-i", str(visual_path)]
                if visual.mode == "motion":
                    visual_duration = visual.end - visual.start
                    still_filter = (
                        f"trim=duration={visual_duration:.4f},"
                        f"setpts=PTS-STARTPTS+{visual.start:.4f}/TB"
                    )
                else:
                    still_filter = (
                        f"trim=duration={frame_duration:.4f},setpts=PTS-STARTPTS,"
                        f"tpad=stop_mode=clone:stop_duration={duration:.4f}"
                    )
            visual_map = f"{next_input}:v:0"
            next_input += 1
            still_name = f"still{visual_index}"
            graph.append(
                f"[{visual_map}]{still_filter},"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={CARD_COLOUR},"
                f"setsar=1,fps={fps},format=yuv420p[{still_name}]"
            )
            visual_name = f"visual{visual_index}"
            enable = f"between(t\\,{visual.start:.4f}\\,{visual.end:.4f})"
            graph.append(
                f"[{current}][{still_name}]overlay=0:0:eof_action=pass:"
                f"enable='{enable}'[{visual_name}]"
            )
            current = visual_name

            cmd += ["-loop", "1", "-framerate", f"{fps:g}", "-i", str(credit)]
            credit_map = f"{next_input}:v:0"
            next_input += 1
            credited_name = f"credited{visual_index}"
            graph.append(
                f"[{current}][{credit_map}]overlay=x=W-w-{margin}:y=H-h-{margin}:"
                f"enable='{enable}'[{credited_name}]"
            )
            current = credited_name
        if label is not None:
            cmd += ["-loop", "1", "-framerate", f"{fps:g}", "-i", str(label)]
            label_map = f"{next_input}:v:0"
            next_input += 1
            label_y = str(margin) if height > width else f"H-h-{margin}"
            graph.append(f"[{current}][{label_map}]overlay=x={margin}:y={label_y}[labeled]")
            current = "labeled"
        if watermark is not None:
            cmd += ["-loop", "1", "-framerate", f"{fps:g}", "-i", str(watermark)]
            watermark_map = f"{next_input}:v:0"
            graph.append(f"[{current}][{watermark_map}]overlay=x=W-w-{margin}:y={margin}[branded]")
            current = "branded"
        cmd += [
            "-filter_complex",
            ";".join(graph),
            "-map",
            f"[{current}]",
            "-map",
            audio_map,
        ]
    else:
        cmd += ["-map", video_map, "-map", audio_map, "-filter:v", vfilter]
    cmd += ["-filter:a", ",".join(achain)]
    cmd += _flags(_VIDEO_ENC, _AUDIO_ENC)
    return cmd + ["-t", f"{duration:.4f}", "-movflags", "+faststart", str(dst)]


def _map(name: str) -> str:
    # `-map` takes raw stream specifiers bare (`0:v`) but filter outputs bracketed.
    return name if ":" in name else f"[{name}]"


def _concat_entry(path: Path) -> str:
    # concat demuxer syntax: single-quoted path, a literal quote written as '\''.
    quoted = str(path).replace("'", "'\\''")
    return f"file '{quoted}'\n"


def _subtitle_filter(burn: Path, profile: RenderProfile) -> str:
    base = f"subtitles=filename={burn.name}"
    if profile != "social":
        return base
    style = (
        "FontName=Arial,FontSize=10,PrimaryColour=&H00FFFFFF,"
        "BackColour=&HA0000000,BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=48"
    )
    return f"{base}:force_style='{style}'"


def _concat_cmd(
    parts: Sequence[Path],
    dst: Path,
    burn: Path | None,
    listfile: Path,
    profile: RenderProfile,
) -> list[str]:
    listfile.write_text("".join(_concat_entry(p) for p in parts), encoding="utf-8")
    cmd = [ffmpeg_bin(), *_BASE]
    cmd += ["-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", str(listfile)]
    if burn is not None:
        # Burning touches pixels, so video has to be re-encoded; audio can ride through.
        cmd += ["-filter:v", _subtitle_filter(burn, profile)]
        cmd += _flags(_VIDEO_ENC) + ["-c:a", "copy"]
    else:
        # Intermediates already share codecs/timebase, so this is a lossless join.
        cmd += ["-c", "copy"]
    return cmd + ["-movflags", "+faststart", str(dst)]


def _xfade_cmd(
    parts: Sequence[Path],
    durations: Sequence[float],
    fade: float,
    dst: Path,
    burn: Path | None,
    profile: RenderProfile,
) -> list[str]:
    cmd = [ffmpeg_bin(), *_BASE]
    for part in parts:
        cmd += ["-i", str(part)]

    graph: list[str] = []
    vlab, alab = "0:v", "0:a"
    # Each transition consumes `fade` seconds of both neighbours, so the offset
    # of transition i sits `fade` before the running (already shortened) end.
    acc = durations[0]
    for i in range(1, len(parts)):
        offset = acc - fade
        graph.append(
            f"[{vlab}][{i}:v]xfade=transition=fade:duration={fade:.4f}:offset={offset:.4f}[v{i}]"
        )
        graph.append(f"[{alab}][{i}:a]acrossfade=d={fade:.4f}:c1=tri:c2=tri[a{i}]")
        vlab, alab = f"v{i}", f"a{i}"
        acc += durations[i] - fade

    if burn is not None:
        graph.append(f"[{vlab}]{_subtitle_filter(burn, profile)}[vout]")
        vlab = "vout"

    if graph:
        cmd += ["-filter_complex", ";".join(graph)]
    cmd += ["-map", _map(vlab), "-map", _map(alab)]
    cmd += _flags(_VIDEO_ENC, _AUDIO_ENC)
    return cmd + ["-movflags", "+faststart", str(dst)]


def _chunk_text(text: str, limit: int = _SRT_LINE) -> list[str]:
    chunks: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > limit:
            chunks.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        chunks.append(current)
    return chunks


def _timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_srt(edl: EDL, offsets: Sequence[float], durations: Sequence[float]) -> str:
    """Rebase each clip's text onto the output timeline.

    Source cue timings are useless here: the clip has been moved, and with a
    crossfade it has been shortened too. Instead the clip's text is split into
    readable chunks and given a share of the clip's output duration proportional
    to its length.
    """
    blocks: list[str] = []
    for clip, offset, duration in zip(edl.clips, offsets, durations, strict=False):
        chunks = _chunk_text(" ".join(clip.text.split()))
        if not chunks:
            continue
        total = sum(len(c) for c in chunks) or 1
        cursor = offset
        for chunk in chunks:
            span = duration * len(chunk) / total
            blocks.append(
                f"{len(blocks) + 1}\n"
                f"{_timestamp(cursor)} --> {_timestamp(min(cursor + span, offset + duration))}\n"
                f"{chunk}\n"
            )
            cursor += span
    return "\n".join(blocks)


def render(
    edl: EDL,
    out_path: Path,
    *,
    crossfade: float = 0.0,
    normalise: bool = True,
    subtitles: Literal["none", "sidecar", "burn"] = "sidecar",
    source_label: bool = True,
    watermark: bool = True,
    watermark_text: str = "MASHUP",
    profile: RenderProfile = "source",
    workdir: Path,
    progress: Progress | None = None,
) -> Path:
    """Cut, normalise and concatenate every clip in `edl` into one MP4.

    `progress` receives short status strings; nothing is printed.
    Returns `out_path`.
    """
    say: Progress = progress or (lambda _msg: None)
    if not edl.clips:
        raise ValueError("cannot render an EDL with no clips")
    if profile not in {"source", "social"}:
        raise ValueError(f"unsupported render profile: {profile}")
    # ffmpeg is invoked with cwd=workdir so the concat list can use relative
    # entries, which means a relative output path would resolve against the
    # workdir rather than the caller's directory. Pin it before anything runs.
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if subtitles == "burn" and not has_subtitles_filter():
        raise ToolError(
            "this ffmpeg build has no `subtitles` filter (it needs libass), so "
            "subtitles cannot be burned in. Use subtitles='sidecar', or install a "
            "libass-enabled ffmpeg (brew install ffmpeg --with-libass / a full build)."
        )

    source_paths = {
        path
        for clip in edl.clips
        for path in (clip.source_path, *(visual.source_path for visual in clip.visuals))
    }
    missing = sorted(path for path in source_paths if not Path(path).exists())
    if missing:
        raise MissingSourceError("missing source media:\n  " + "\n  ".join(missing))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(workdir)
    parts_dir = workdir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    labels_dir = workdir / "source-labels"

    infos = {p: probe(Path(p)) for p in sorted({c.source_path for c in edl.clips})}
    size, fps = _target_format(edl, infos, profile)
    say(f"target {size[0]}x{size[1]}@{fps:g}, {len(edl.clips)} clips")

    parts: list[Path] = []
    for n, clip in enumerate(edl.clips, start=1):
        key = _clip_key(
            clip,
            size,
            fps,
            normalise,
            source_label,
            watermark,
            watermark_text,
            profile,
        )
        dst = parts_dir / f"{key}.mp4"
        tag = f"[{n}/{len(edl.clips)}] {clip.source_id} {clip.render_start:.2f}s"
        if dst.exists() and dst.stat().st_size > 0:
            say(f"{tag} cached")
        else:
            say(f"{tag} extracting")
            info = infos[clip.source_path]
            label = write_label_card(clip, size[0], labels_dir) if source_label else None
            watermark_card = (
                write_watermark_card(watermark_text, size[0], labels_dir) if watermark else None
            )
            visual_credits = [
                write_visual_credit_card(visual, size[0], labels_dir) for visual in clip.visuals
            ]
            run_tool(
                _extract_cmd(
                    clip,
                    info,
                    dst,
                    size=size,
                    fps=fps,
                    normalise=normalise,
                    label=label,
                    watermark=watermark_card,
                    visual_credits=visual_credits,
                )
            )
        parts.append(dst)

    # Probe the real intermediates: encoders round to frame boundaries, and the
    # xfade offsets have to match what is actually on disk.
    durations = [probe(p).duration for p in parts]

    fade = 0.0
    if crossfade > 0 and len(parts) > 1:
        # A crossfade longer than half the shortest clip would swallow it whole.
        fade = min(crossfade, min(durations) / 2)

    offsets: list[float] = []
    cursor = 0.0
    for i, duration in enumerate(durations):
        offsets.append(cursor)
        cursor += duration - (fade if i + 1 < len(durations) else 0.0)

    srt_text = _build_srt(edl, offsets, durations) if subtitles != "none" else ""
    burn_path: Path | None = None
    if subtitles != "none" and srt_text:
        sidecar = out_path.with_suffix(".srt")
        sidecar.write_text(srt_text, encoding="utf-8")
        say(f"subtitles {sidecar.name}")
        if subtitles == "burn":
            # Run ffmpeg from the workdir with a bare filename: the `subtitles`
            # filter needs colons and backslashes in paths escaped otherwise.
            burn_path = workdir / "burn.srt"
            burn_path.write_text(srt_text, encoding="utf-8")

    say("concatenating" + (f" with {fade:.2f}s crossfades" if fade else ""))
    if fade:
        cmd = _xfade_cmd(parts, durations, fade, out_path, burn_path, profile)
    else:
        cmd = _concat_cmd(parts, out_path, burn_path, workdir / "concat.txt", profile)
    run_tool(cmd, cwd=workdir)

    # Crossfades overlap material, so the sum of clip durations overstates the
    # result; report what the container actually says.
    say(f"wrote {out_path.name} ({probe(out_path).duration:.2f}s)")
    return out_path
