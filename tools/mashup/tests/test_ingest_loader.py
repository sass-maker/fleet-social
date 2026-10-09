from pathlib import Path
from types import SimpleNamespace

import pytest

from mashup.ingest import loader
from mashup.ingest.loader import IngestError
from mashup.ingest.media import MediaError


def create_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "bad.mp4").write_bytes(b"unreadable")
    (archive / "good.mp4").write_bytes(b"synthetic")
    return archive


def test_archive_can_skip_one_unreadable_source_and_report_it(tmp_path, monkeypatch):
    archive = create_archive(tmp_path)
    reported: list[tuple[str, str]] = []

    def fake_ingest(path, *, ordinal, **_options):
        if path.name == "bad.mp4":
            raise MediaError("invalid container")
        return SimpleNamespace(id="good", ordinal=ordinal), []

    monkeypatch.setattr(loader, "ingest_source", fake_ingest)

    items = loader.ingest_archive(
        archive,
        workdir=tmp_path / "work",
        allow_transcribe=False,
        skip_unreadable=True,
        on_error=lambda path, error: reported.append((path.name, str(error))),
    )

    assert items[0][0].id == "good"
    assert items[0][0].ordinal == 1
    assert reported == [("bad.mp4", "invalid container")]


def test_archive_stays_strict_by_default_and_fails_when_every_source_is_bad(tmp_path, monkeypatch):
    archive = create_archive(tmp_path)

    def fail(_path, **_options):
        raise MediaError("invalid container")

    monkeypatch.setattr(loader, "ingest_source", fail)

    with pytest.raises(MediaError, match="invalid container"):
        loader.ingest_archive(
            archive,
            workdir=tmp_path / "work",
            allow_transcribe=False,
        )

    with pytest.raises(IngestError, match="2 failed"):
        loader.ingest_archive(
            archive,
            workdir=tmp_path / "work",
            allow_transcribe=False,
            skip_unreadable=True,
        )
