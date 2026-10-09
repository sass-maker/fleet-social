"""The `mashup feed` and `mashup fetch-episode` surfaces, exercised offline.

The commands build their own HTTP client, so the seam these tests replace is
`mashup.cli._fetcher` — which exists for exactly that reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import StubFetcher
from typer.testing import CliRunner

from mashup.cli import app

pytestmark = pytest.mark.usefixtures("no_network")

FEEDS = Path(__file__).resolve().parent / "feeds"
KETTLE_URL = "https://feeds.example.org/kettle/rss"
BROKEN_URL = "https://feeds.example.org/broken/rss"
ARR_URL = "https://feeds.example.org/arr/rss"
AUDIO = b"ID3 fake mp3 payload, " + b"x" * 4096

# Wide enough that rich never wraps a path mid-assertion.
runner = CliRunner(env={"COLUMNS": "300", "TERM": "dumb", "NO_COLOR": "1"})


def xml(name: str) -> bytes:
    return (FEEDS / f"{name}.xml").read_bytes()


@pytest.fixture
def fetcher(monkeypatch) -> StubFetcher:
    stub = StubFetcher(
        {
            KETTLE_URL: xml("basic"),
            BROKEN_URL: xml("adversarial"),
            ARR_URL: xml("unlicensed"),
            "https://cdn.example.org/kettle/003.mp3": AUDIO,
            "https://cdn.example.org/kettle/002.m4a": AUDIO,
            "https://cdn.example.org/kettle/001.mp3": AUDIO,
            "https://cdn.example.org/broken/1.mp3": AUDIO,
            "https://feeds.example.org/audio/relative.mp3": AUDIO,
            "https://cdn.example.org/broken/6.bin": AUDIO,
            "https://cdn.example.org/arr/1.mp3": AUDIO,
        }
    )
    monkeypatch.setattr("mashup.cli._fetcher", lambda: stub)
    return stub


def invoke(*args, workdir: Path | None = None):
    argv = list(args)
    if workdir is not None:
        argv += ["--workdir", str(workdir)]
    return runner.invoke(app, argv)


# --------------------------------------------------------------------------
# feed
# --------------------------------------------------------------------------


def test_feed_lists_the_episodes_without_downloading_anything(fetcher):
    result = invoke("feed", "--url", KETTLE_URL)
    assert result.exit_code == 0, result.output + result.stderr
    assert "The Kettle Logic Show" in result.output
    assert "kettle-0003" in result.output
    assert "1:02:03" in result.output
    assert "2025-03-12" in result.output
    assert [call[0] for call in fetcher.calls] == ["get", "close"]


def test_feed_shows_the_licence_it_would_acquire_under(fetcher):
    result = invoke("feed", "--url", KETTLE_URL)
    assert "creativecommons.org/licenses/by/4.0" in result.output


def test_feed_warns_rather_than_fails_on_an_unlicensed_feed(fetcher):
    """Listing is not acquisition; the gate belongs on the download."""
    result = invoke("feed", "--url", ARR_URL)
    assert result.exit_code == 0
    assert "no machine-readable licence" in result.stderr


def test_feed_reports_the_entries_it_skipped(fetcher):
    result = invoke("feed", "--url", BROKEN_URL)
    assert result.exit_code == 0
    assert "no enclosure" in result.stderr
    assert "duplicate guid" in result.stderr
    assert "unfetchable enclosure URL" in result.stderr


def test_feed_limit_truncates_the_listing(fetcher):
    result = invoke("feed", "--url", KETTLE_URL, "--limit", "1")
    assert "kettle-0003" in result.output
    assert "kettle-0001" not in result.output
    assert "2 more" in result.output


def test_feed_rejects_a_page_budget_below_one(fetcher):
    assert invoke("feed", "--url", KETTLE_URL, "--pages", "0").exit_code == 2


# --------------------------------------------------------------------------
# fetch-episode
# --------------------------------------------------------------------------


def test_fetch_episode_caches_the_audio_and_writes_the_record(fetcher, tmp_path):
    result = invoke("fetch-episode", "--url", KETTLE_URL, "--guid", "kettle-0003", workdir=tmp_path)
    assert result.exit_code == 0, result.output + result.stderr
    assert "downloaded" in result.output

    records = list((tmp_path / "cache" / "episodes").rglob("acquisition.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["episode"]["guid"] == "kettle-0003"
    assert Path(record["audio"]["path"]).read_bytes() == AUDIO
    assert record["audio"]["sha256"] in result.output


def test_fetch_episode_accepts_an_index_instead_of_a_guid(fetcher, tmp_path):
    result = invoke("fetch-episode", "--url", KETTLE_URL, "--index", "2", workdir=tmp_path)
    assert result.exit_code == 0, result.output + result.stderr
    record = json.loads(next((tmp_path / "cache").rglob("acquisition.json")).read_text())
    assert record["episode"]["guid"] == "kettle-0002"


def test_rerunning_fetch_episode_is_a_cache_hit(fetcher, tmp_path):
    invoke("fetch-episode", "--url", KETTLE_URL, "--guid", "kettle-0003", workdir=tmp_path)
    fetcher.calls.clear()
    result = invoke("fetch-episode", "--url", KETTLE_URL, "--guid", "kettle-0003", workdir=tmp_path)
    assert result.exit_code == 0
    assert "cached" in result.output
    assert [call[0] for call in fetcher.calls] == ["get", "close"]


def test_fetch_episode_points_at_the_existing_ingest_command(fetcher, tmp_path):
    result = invoke("fetch-episode", "--url", KETTLE_URL, "--guid", "kettle-0003", workdir=tmp_path)
    assert "mashup ingest --input" in result.output


def test_fetch_episode_refuses_a_feed_whose_rights_are_unknown(fetcher, tmp_path):
    result = invoke("fetch-episode", "--url", ARR_URL, "--guid", "arr-1", workdir=tmp_path)
    assert result.exit_code == 2
    assert "refusing to fetch" in result.stderr
    assert not list(tmp_path.rglob("acquisition.json"))


def test_the_rights_override_records_that_it_was_used(fetcher, tmp_path):
    result = invoke(
        "fetch-episode", "--url", ARR_URL, "--guid", "arr-1", "--i-have-rights", workdir=tmp_path
    )
    assert result.exit_code == 0, result.output + result.stderr
    record = json.loads(next(tmp_path.rglob("acquisition.json")).read_text())
    assert record["rights"]["override"] is True
    assert record["rights"]["license"] == "unspecified (--i-have-rights)"


def test_fetch_episode_warns_when_the_cache_key_is_borrowed(fetcher, tmp_path):
    result = invoke("fetch-episode", "--url", BROKEN_URL, "--index", "2", workdir=tmp_path)
    assert result.exit_code == 0, result.output + result.stderr
    assert "publishes no <guid>" in result.stderr


def test_fetch_episode_needs_exactly_one_selector(fetcher, tmp_path):
    neither = invoke("fetch-episode", "--url", KETTLE_URL, workdir=tmp_path)
    both = invoke(
        "fetch-episode",
        "--url",
        KETTLE_URL,
        "--guid",
        "kettle-0003",
        "--index",
        "1",
        workdir=tmp_path,
    )
    assert neither.exit_code == 2 and both.exit_code == 2
    assert "exactly one" in neither.stderr


def test_fetch_episode_rejects_an_unknown_guid(fetcher, tmp_path):
    result = invoke("fetch-episode", "--url", KETTLE_URL, "--guid", "nope", workdir=tmp_path)
    assert result.exit_code == 2
    assert "no episode with guid" in result.stderr


def test_fetch_episode_rejects_an_out_of_range_index(fetcher, tmp_path):
    result = invoke("fetch-episode", "--url", KETTLE_URL, "--index", "9", workdir=tmp_path)
    assert result.exit_code == 2
    assert "between 1 and 3" in result.stderr


def test_a_dead_feed_url_exits_one_not_zero(monkeypatch, tmp_path):
    stub = StubFetcher({}, errors={KETTLE_URL: OSError("no route to host")})
    monkeypatch.setattr("mashup.cli._fetcher", lambda: stub)
    result = invoke("feed", "--url", KETTLE_URL)
    assert result.exit_code == 1
    assert "no route to host" in result.stderr
