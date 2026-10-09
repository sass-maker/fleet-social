"""Local subtitle generation for archives that ship without SRT/VTT.

Two backends, both optional and both imported/resolved lazily so nothing else
in the pipeline fails to load on a machine that will never transcribe:

- `whisperkit` — the `whisperkit-cli` binary (`brew install whisperkit-cli`),
  running Apple's CoreML Whisper builds on the Neural Engine. Roughly 16x
  realtime warm on an M-series, and it emits SRT directly.
- `mlx` — the `mlx-whisper` Python extra (`uv sync --extra transcribe`).

`auto` prefers whisperkit when the binary is present, because it is the faster
of the two and needs no Python dependency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

DEFAULT_MODEL = "mlx-community/whisper-small.en-mlx"
# Whisper's own front end resamples to 16 kHz mono; doing it in ffmpeg keeps the
# temp file small and avoids handing whisper a video container to demux.
SAMPLE_RATE = 16000

Backend = Literal["auto", "whisperkit", "mlx"]

# A local CoreML model directory for whisperkit-cli. Without one the CLI would
# download a model; pointing at an existing directory keeps transcription
# offline and lets the caller choose the variant.
WHISPERKIT_MODEL_ENV = "MASHUP_WHISPERKIT_MODEL"

_MISSING_EXTRA = (
    "No transcription backend available.\n"
    "  brew install whisperkit-cli        (fastest; Apple silicon)\n"
    "  uv sync --extra transcribe         (mlx-whisper)\n"
    "Otherwise place a sibling .srt/.vtt next to the media file."
)


class TranscribeError(RuntimeError):
    """Raised when transcription cannot run or produce an SRT."""


def _resolve_backend(backend: Backend) -> str:
    if backend != "auto":
        return backend
    return "whisperkit" if shutil.which("whisperkit-cli") else "mlx"


def transcribe(
    media: Path,
    out_srt: Path,
    model: str = DEFAULT_MODEL,
    *,
    backend: Backend = "auto",
    whisperkit_model: str | None = None,
    language: str = "en",
) -> Path:
    """Transcribe `media` to `out_srt`, returning the SRT path.

    Transcription is the slowest step in the pipeline by an order of magnitude,
    so an existing output is always trusted and re-used.
    """
    if out_srt.exists():
        return out_srt
    if not media.is_file():
        raise TranscribeError(f"Media file not found: {media}")

    out_srt.parent.mkdir(parents=True, exist_ok=True)
    resolved = _resolve_backend(backend)
    if resolved == "whisperkit":
        text = _transcribe_whisperkit(media, whisperkit_model, language)
    else:
        text = _transcribe_mlx(media, model)

    # Write via a sibling temp file: a half-written SRT left by a crash would be
    # silently trusted by the resume check above.
    staging = out_srt.with_suffix(out_srt.suffix + ".partial")
    staging.write_text(text, encoding="utf-8")
    staging.replace(out_srt)
    return out_srt


def _transcribe_whisperkit(media: Path, model_dir: str | None, language: str) -> str:
    if shutil.which("whisperkit-cli") is None:
        raise TranscribeError(_MISSING_EXTRA)
    model_dir = model_dir or os.getenv(WHISPERKIT_MODEL_ENV) or ""

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        wav = tmpdir / "audio.wav"
        _extract_audio(media, wav)
        cmd = [
            "whisperkit-cli",
            "transcribe",
            "--audio-path",
            str(wav),
            "--language",
            language,
            # Measured on a 5-minute slice of the dev archive: `vad` re-emits
            # whole decoded windows, giving 49% duplicate cues (identical
            # internal whisper timestamps at advancing wall-clock times).
            # `none` gives 2%, and those are genuine repeats in the audio.
            # Duplicate cues would poison the non-repetition scoring term.
            "--chunking-strategy",
            "none",
            "--report",
            "--report-path",
            str(tmpdir),
        ]
        if model_dir:
            cmd += ["--model-path", str(Path(model_dir).expanduser())]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise TranscribeError(f"whisperkit-cli failed on {media}: {proc.stderr.strip()[-500:]}")
        srt = wav.with_suffix(".srt")
        if not srt.is_file():
            raise TranscribeError(f"whisperkit-cli produced no SRT for {media}")
        # Whisper special tokens are stripped downstream by the subtitle parser.
        return srt.read_text(encoding="utf-8")


def _transcribe_mlx(media: Path, model: str) -> str:
    try:
        import mlx_whisper  # noqa: PLC0415 — optional extra, see module docstring
    except ImportError as exc:
        raise TranscribeError(_MISSING_EXTRA) from exc

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        _extract_audio(media, wav)
        result: dict[str, Any] = mlx_whisper.transcribe(
            str(wav),
            path_or_hf_repo=model,
            # Word timings triple the runtime and the renderer only ever cuts on
            # segment boundaries.
            word_timestamps=False,
        )

    segments = result.get("segments") or []
    if not segments:
        raise TranscribeError(f"Transcription produced no segments for {media}")
    return _to_srt(segments)


def _extract_audio(media: Path, wav: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise TranscribeError("ffmpeg not found on PATH. Install FFmpeg (`brew install ffmpeg`).")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(media),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "wav",
            str(wav),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not wav.is_file():
        raise TranscribeError(f"ffmpeg failed to extract audio from {media}: {proc.stderr.strip()}")


def _to_srt(segments: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = float(segment.get("start", 0.0))
        end = max(float(segment.get("end", start)), start)
        blocks.append(f"{len(blocks) + 1}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n")
    return "\n".join(blocks)


def _stamp(seconds: float) -> str:
    millis = int(round(max(seconds, 0.0) * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
