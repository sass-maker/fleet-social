"""Offline tests for scripts/fetch_archive.py — no network, no downloads."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_archive.py"
_spec = importlib.util.spec_from_file_location("fetch_archive", SCRIPT)
assert _spec and _spec.loader
fa = importlib.util.module_from_spec(_spec)
sys.modules["fetch_archive"] = fa
_spec.loader.exec_module(fa)


PD_MARK = "http://creativecommons.org/publicdomain/mark/1.0/"

FILES = [
    {"name": "02-Bird.mp4", "size": "200", "md5": "b" * 32, "format": "MPEG4"},
    {"name": "01-Chair4.mp4", "size": "100", "md5": "a" * 32, "format": "MPEG4"},
    {"name": "03-Duck.mp4", "size": "300", "md5": "c" * 32, "format": "MPEG4"},
    {"name": "01-Chair4.ogv", "size": "90", "md5": "d" * 32, "format": "Ogg Video"},
    {"name": "thumb.jpg", "size": "5", "format": "Thumbnail"},
]


def item_metadata(licenseurl: str | None = PD_MARK) -> dict:
    md: dict = {"identifier": "ybylcollection", "title": "You Bet Your Life Collection"}
    if licenseurl is not None:
        md["licenseurl"] = licenseurl
    return {"metadata": md, "files": FILES}


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "test"})


# --------------------------------------------------------------------------
# select_files
# --------------------------------------------------------------------------


def test_select_files_filters_sorts_and_limits():
    picked = fa.select_files(FILES, fmt="MPEG4", limit=2)
    assert [f.name for f in picked] == ["01-Chair4.mp4", "02-Bird.mp4"]
    assert picked[0].size == 100 and picked[0].md5 == "a" * 32


def test_select_files_no_limit_returns_all_of_format():
    assert len(fa.select_files(FILES, fmt="MPEG4")) == 3
    assert len(fa.select_files(FILES, fmt="Ogg Video")) == 1
    assert fa.select_files(FILES, fmt="FLAC") == []


def test_select_files_limit_larger_than_available():
    assert len(fa.select_files(FILES, fmt="MPEG4", limit=99)) == 3


def test_url_name_percent_encodes_spaces():
    f = fa.ArchiveFile(name="You Bet #3.mp4", size=1, md5=None, format="MPEG4")
    assert f.url_name == "You%20Bet%20%233.mp4"


# --------------------------------------------------------------------------
# check_license
# --------------------------------------------------------------------------


def test_check_license_accepts_public_domain_mark():
    assert fa.check_license(item_metadata()["metadata"]) == PD_MARK


@pytest.mark.parametrize(
    "url",
    [
        "https://creativecommons.org/licenses/by-nd/4.0/",
        "https://creativecommons.org/licenses/by-nc-nd/4.0/",
    ],
)
def test_check_license_refuses_nd(url):
    with pytest.raises(fa.LicenseError, match="forbids derivative"):
        fa.check_license({"licenseurl": url})


def test_check_license_accepts_cc_by_sa():
    url = "https://creativecommons.org/licenses/by-sa/4.0/"
    assert fa.check_license({"licenseurl": url}) == url


def test_check_license_refuses_missing_and_unknown():
    with pytest.raises(fa.LicenseError, match="no licenseurl"):
        fa.check_license({})
    with pytest.raises(fa.LicenseError, match="unrecognised"):
        fa.check_license({"licenseurl": "https://example.com/my-terms"})


def test_check_license_override_allows_anything():
    assert fa.check_license({"licenseurl": "…/by-nd/4.0/"}, override=True) == "…/by-nd/4.0/"
    assert "i-have-rights" in fa.check_license({}, override=True)


def test_check_license_handles_list_valued_field():
    assert fa.check_license({"licenseurl": [PD_MARK]}) == PD_MARK


def test_main_exits_3_on_nd_license(tmp_path, monkeypatch, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=item_metadata("https://creativecommons.org/licenses/by-nd/4.0/")
        )

    _patch_client(monkeypatch, handler)
    code = fa.main(["--item", "x", "--dest", str(tmp_path / "out")])
    assert code == fa.EXIT_LICENSE
    assert "refusing" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# --------------------------------------------------------------------------
# plan_downloads / resume
# --------------------------------------------------------------------------


def test_plan_marks_complete_file_done(tmp_path):
    (tmp_path / "01-Chair4.mp4").write_bytes(b"x" * 100)
    plans = fa.plan_downloads(fa.select_files(FILES, limit=1), tmp_path)
    assert plans[0].done and plans[0].remaining == 0 and plans[0].range_header == {}


def test_plan_ignores_wrong_sized_existing_file(tmp_path):
    (tmp_path / "01-Chair4.mp4").write_bytes(b"x" * 42)
    assert fa.plan_downloads(fa.select_files(FILES, limit=1), tmp_path)[0].done is False


def test_plan_resume_computes_range_header(tmp_path):
    (tmp_path / "01-Chair4.mp4.part").write_bytes(b"x" * 30)
    plan = fa.plan_downloads(fa.select_files(FILES, limit=1), tmp_path)[0]
    assert plan.resume_from == 30
    assert plan.range_header == {"Range": "bytes=30-"}
    assert plan.remaining == 70


def test_plan_fresh_download_has_no_range_header(tmp_path):
    plan = fa.plan_downloads(fa.select_files(FILES, limit=1), tmp_path)[0]
    assert plan.resume_from == 0 and plan.range_header == {} and plan.remaining == 100


def test_plan_discards_oversized_partial(tmp_path):
    (tmp_path / "01-Chair4.mp4.part").write_bytes(b"x" * 500)
    assert fa.plan_downloads(fa.select_files(FILES, limit=1), tmp_path)[0].resume_from == 0


def test_download_resume_sends_range_and_appends(tmp_path):
    body = bytes(range(256)) * 4  # 1024 bytes
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["range"] = request.headers.get("range", "")
        start = int(seen["range"].split("=")[1].split("-")[0])
        return httpx.Response(206, content=body[start:])

    f = fa.ArchiveFile("clip.mp4", len(body), hashlib.md5(body).hexdigest(), "MPEG4")
    (tmp_path / "clip.mp4.part").write_bytes(body[:400])
    plan = fa.plan_downloads([f], tmp_path)[0]

    with mock_client(handler) as client:
        fa.download_one(client, "item", plan)

    assert seen["range"] == "bytes=400-"
    assert (tmp_path / "clip.mp4").read_bytes() == body
    assert not (tmp_path / "clip.mp4.part").exists()


def test_download_restarts_when_server_ignores_range(tmp_path):
    body = b"z" * 512

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)  # full body despite Range

    f = fa.ArchiveFile("clip.mp4", len(body), hashlib.md5(body).hexdigest(), "MPEG4")
    (tmp_path / "clip.mp4.part").write_bytes(b"z" * 100)
    plan = fa.plan_downloads([f], tmp_path)[0]

    with mock_client(handler) as client:
        fa.download_one(client, "item", plan)

    assert (tmp_path / "clip.mp4").read_bytes() == body


# --------------------------------------------------------------------------
# md5 verification
# --------------------------------------------------------------------------


def test_md5_mismatch_deletes_partial_and_raises(tmp_path):
    body = b"corrupted payload"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    f = fa.ArchiveFile("clip.mp4", len(body), "0" * 32, "MPEG4")
    plan = fa.plan_downloads([f], tmp_path)[0]

    with mock_client(handler) as client, pytest.raises(fa.ChecksumError, match="md5 mismatch"):
        fa.download_one(client, "item", plan)

    assert not (tmp_path / "clip.mp4.part").exists()
    assert not (tmp_path / "clip.mp4").exists()


def test_md5_match_promotes_part_to_final(tmp_path):
    body = b"good payload"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    f = fa.ArchiveFile("clip.mp4", len(body), hashlib.md5(body).hexdigest(), "MPEG4")
    plan = fa.plan_downloads([f], tmp_path)[0]
    with mock_client(handler) as client:
        fa.download_one(client, "item", plan)
    assert (tmp_path / "clip.mp4").read_bytes() == body


def test_main_exits_4_on_checksum_mismatch(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "metadata" in str(request.url):
            return httpx.Response(200, json=item_metadata())
        return httpx.Response(200, content=b"x" * 100)  # right size, wrong md5

    _patch_client(monkeypatch, handler)
    code = fa.main(["--item", "y", "--dest", str(tmp_path), "--limit", "1", "--sleep", "0"])
    assert code == fa.EXIT_CHECKSUM
    assert not (tmp_path / "PROVENANCE.json").exists()


# --------------------------------------------------------------------------
# dry run + provenance
# --------------------------------------------------------------------------


def _patch_client(monkeypatch, handler) -> None:
    real = httpx.Client
    monkeypatch.setattr(
        fa.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )


def test_dry_run_writes_nothing_and_reports_total(tmp_path, monkeypatch, capsys):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=item_metadata())

    _patch_client(monkeypatch, handler)
    dest = tmp_path / "archive"
    assert (
        fa.main(["--item", "ybylcollection", "--dest", str(dest), "--limit", "2", "--dry-run"]) == 0
    )

    out = capsys.readouterr().out
    assert "01-Chair4.mp4" in out and "02-Bird.mp4" in out and "03-Duck.mp4" not in out
    assert "300 bytes" in out  # 100 + 200
    assert not dest.exists()
    assert len(calls) == 1 and "metadata" in calls[0]


def test_full_run_writes_provenance(tmp_path, monkeypatch):
    payloads = {"01-Chair4.mp4": b"a" * 100, "02-Bird.mp4": b"b" * 200}
    files = [
        {"name": n, "size": str(len(p)), "md5": hashlib.md5(p).hexdigest(), "format": "MPEG4"}
        for n, p in payloads.items()
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "metadata" in str(request.url):
            return httpx.Response(
                200, json={"metadata": item_metadata()["metadata"], "files": files}
            )
        return httpx.Response(200, content=payloads[str(request.url).rsplit("/", 1)[-1]])

    _patch_client(monkeypatch, handler)
    assert fa.main(["--item", "ybylcollection", "--dest", str(tmp_path), "--sleep", "0"]) == 0

    prov = json.loads((tmp_path / "PROVENANCE.json").read_text())
    assert prov["item"] == "ybylcollection"
    assert prov["licenseurl"] == PD_MARK
    assert prov["source_url"] == "https://archive.org/details/ybylcollection"
    assert prov["title"] == "You Bet Your Life Collection"
    assert prov["fetched_at"] and [f["name"] for f in prov["files"]] == list(payloads)
    assert (tmp_path / "01-Chair4.mp4").read_bytes() == payloads["01-Chair4.mp4"]


def test_second_run_skips_complete_files(tmp_path, monkeypatch):
    body = b"a" * 100
    files = [
        {
            "name": "01-Chair4.mp4",
            "size": "100",
            "md5": hashlib.md5(body).hexdigest(),
            "format": "MPEG4",
        }
    ]
    downloads: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "metadata" in str(request.url):
            return httpx.Response(
                200, json={"metadata": item_metadata()["metadata"], "files": files}
            )
        downloads.append(str(request.url))
        return httpx.Response(200, content=body)

    _patch_client(monkeypatch, handler)
    argv = ["--item", "i", "--dest", str(tmp_path), "--sleep", "0"]
    assert fa.main(argv) == 0
    assert fa.main(argv) == 0
    assert len(downloads) == 1


def test_no_matching_format_exits_1(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=item_metadata())

    _patch_client(monkeypatch, handler)
    code = fa.main(["--item", "i", "--dest", str(tmp_path), "--format", "FLAC"])
    assert code == fa.EXIT_ERROR


def test_metadata_http_error_exits_1(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    _patch_client(monkeypatch, handler)
    assert fa.main(["--item", "nope", "--dest", str(tmp_path)]) == fa.EXIT_ERROR
