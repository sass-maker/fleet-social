from __future__ import annotations

import subprocess
from importlib import import_module
from pathlib import Path

import pytest

transcribe_module = import_module("mashup.ingest.transcribe")


def test_auto_prefers_available_whisperkit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        transcribe_module.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/whisperkit-cli" if name == "whisperkit-cli" else None,
    )
    assert transcribe_module._resolve_backend("auto") == "whisperkit"


def test_whisperkit_backend_writes_atomic_srt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "episode.mp4"
    media.write_bytes(b"fixture")
    output = tmp_path / "episode.srt"
    commands: list[list[str]] = []

    monkeypatch.setattr(
        transcribe_module.shutil, "which", lambda _name: "/opt/homebrew/bin/whisperkit-cli"
    )
    monkeypatch.setattr(
        transcribe_module,
        "_extract_audio",
        lambda _media, wav: wav.write_bytes(b"wav"),
    )

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        wav = Path(command[command.index("--audio-path") + 1])
        wav.with_suffix(".srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(transcribe_module.subprocess, "run", fake_run)

    assert (
        transcribe_module.transcribe(
            media,
            output,
            backend="whisperkit",
            whisperkit_model="/models/whisper-small",
        )
        == output
    )
    assert output.read_text(encoding="utf-8").endswith("hello\n")
    assert not output.with_suffix(".srt.partial").exists()
    assert commands[0][0:2] == ["whisperkit-cli", "transcribe"]
    assert commands[0][commands[0].index("--model-path") + 1] == "/models/whisper-small"
    assert commands[0][commands[0].index("--chunking-strategy") + 1] == "none"


def test_missing_whisperkit_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "episode.mp4"
    media.write_bytes(b"fixture")
    monkeypatch.setattr(transcribe_module.shutil, "which", lambda _name: None)

    with pytest.raises(transcribe_module.TranscribeError, match="No transcription backend"):
        transcribe_module.transcribe(
            media,
            tmp_path / "episode.srt",
            backend="whisperkit",
        )
