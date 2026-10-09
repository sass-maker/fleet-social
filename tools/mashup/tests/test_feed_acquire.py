"""Offline tests for feed resolution, the rights gate, and the episode cache.

Every test here runs with sockets disabled (`no_network`), so a regression
that reintroduces a real fetch fails loudly rather than quietly costing
bandwidth on someone's CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import StubFetcher

from mashup.feed.acquire import (
    ACQUISITION_SCHEMA,
    AcquireError,
    RightsError,
    acquire_episode,
    audio_suffix,
    cache_key,
    check_rights,
    episode_dir,
    resolve_feed,
    sha256_file,
)
from mashup.feed.parse import parse_feed

pytestmark = pytest.mark.usefixtures("no_network")

FEEDS = Path(__file__).resolve().parent / "feeds"

KETTLE_URL = "https://feeds.example.org/kettle/rss"
PAGED_URL = "https://feeds.example.org/paged/rss"
LOOP_URL = "https://feeds.example.org/loop/rss"

AUDIO = b"ID3 fake mp3 payload, " + b"x" * 4096


def xml(name: str) -> bytes:
    return (FEEDS / f"{name}.xml").read_bytes()


def kettle_fetcher(**kwargs) -> StubFetcher:
    return StubFetcher(
        {
            KETTLE_URL: xml("basic"),
            "https://cdn.example.org/kettle/003.mp3": AUDIO,
            "https://cdn.example.org/kettle/002.m4a": AUDIO,
            "https://cdn.example.org/kettle/001.mp3": AUDIO,
        },
        **kwargs,
    )


def load(name: str, url: str):
    return parse_feed(xml(name), feed_url=url)


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def test_resolve_feed_fetches_one_page_when_there_is_no_next_link():
    fetcher = kettle_fetcher()
    feed = resolve_feed(KETTLE_URL, fetcher=fetcher)
    assert feed.pages == 1
    assert len(feed.episodes) == 3
    assert fetcher.urls == [KETTLE_URL]


def test_resolve_feed_follows_pagination_and_merges_the_pages():
    fetcher = StubFetcher(
        {PAGED_URL: xml("page1"), PAGED_URL + "?page=2": xml("page2")},
    )
    feed = resolve_feed(PAGED_URL, fetcher=fetcher)
    assert feed.pages == 2
    assert [e.guid for e in feed.episodes] == ["paged-3", "paged-2", "paged-1"]
    assert fetcher.urls == [PAGED_URL, PAGED_URL + "?page=2"]


def test_resolve_feed_stops_at_max_pages():
    fetcher = StubFetcher({PAGED_URL: xml("page1"), PAGED_URL + "?page=2": xml("page2")})
    feed = resolve_feed(PAGED_URL, fetcher=fetcher, max_pages=1)
    assert feed.pages == 1
    assert [e.guid for e in feed.episodes] == ["paged-3", "paged-2"]


def test_resolve_feed_does_not_loop_on_a_self_referential_next_link():
    """Otherwise this walks until max_pages on every self-linking feed."""
    fetcher = StubFetcher({LOOP_URL: xml("looping")})
    feed = resolve_feed(LOOP_URL, fetcher=fetcher, max_pages=50)
    assert feed.pages == 1
    assert fetcher.urls == [LOOP_URL]


def test_resolve_feed_parses_relative_urls_against_the_post_redirect_url():
    """A feed that has moved resolves its own relative URLs against where it
    now lives, not against the URL the user pasted."""
    old = "https://old.example.org/feed"
    new = "https://feeds.example.org/broken/rss"
    fetcher = StubFetcher({new: xml("adversarial")}, redirects={old: new})
    feed = resolve_feed(old, fetcher=fetcher)
    episode = next(e for e in feed.episodes if e.title == "Relative and undated")
    assert episode.enclosure_url == "https://feeds.example.org/audio/relative.mp3"


def test_resolve_feed_rejects_a_page_budget_below_one():
    with pytest.raises(ValueError):
        resolve_feed(KETTLE_URL, fetcher=kettle_fetcher(), max_pages=0)


# --------------------------------------------------------------------------
# rights — the gate fails closed
# --------------------------------------------------------------------------


def test_rights_gate_accepts_a_creative_commons_licence():
    assert check_rights(load("basic", KETTLE_URL)) == "https://creativecommons.org/licenses/by/4.0/"


def test_rights_gate_accepts_a_public_domain_dedication():
    feed = load("adversarial", "https://feeds.example.org/broken/rss")
    assert "publicdomain" in check_rights(feed)


def test_rights_gate_refuses_a_feed_with_no_machine_readable_licence():
    feed = load("unlicensed", "https://feeds.example.org/arr/rss")
    with pytest.raises(RightsError, match="no machine-readable licence"):
        check_rights(feed)


def test_rights_gate_refuses_a_no_derivatives_licence():
    feed = load("no-derivatives", "https://feeds.example.org/nd/rss")
    with pytest.raises(RightsError, match="does not clearly permit derivative works"):
        check_rights(feed)


def test_rights_override_is_the_creators_own_escape_hatch():
    feed = load("unlicensed", "https://feeds.example.org/arr/rss")
    assert check_rights(feed, override=True) == "unspecified (--i-have-rights)"


# --------------------------------------------------------------------------
# cache layout
# --------------------------------------------------------------------------


def test_cache_key_separates_the_same_guid_in_two_different_feeds():
    assert cache_key("https://a.example/rss", "g1") != cache_key("https://b.example/rss", "g1")
    assert cache_key("https://a.example/rss", "g1") == cache_key("https://a.example/rss", "g1")


def test_episode_directory_is_readable_and_stable(tmp_path):
    feed = load("basic", KETTLE_URL)
    directory = episode_dir(tmp_path, feed.url, feed.episodes[0])
    assert directory.parent.name == "episodes"
    assert directory.name.startswith("episode-3-the-long-one-")
    assert directory == episode_dir(tmp_path, feed.url, feed.episodes[0])


@pytest.mark.parametrize(
    ("mime", "url", "expected"),
    [
        ("audio/mpeg", "https://cdn.example.org/a", ".mp3"),
        ("audio/mpeg; charset=binary", "https://cdn.example.org/a", ".mp3"),
        ("audio/x-m4a", "https://cdn.example.org/a", ".m4a"),
        (None, "https://cdn.example.org/a.ogg", ".ogg"),
        ("application/octet-stream", "https://cdn.example.org/a.bin", ".mp3"),
        ("application/octet-stream", "https://cdn.example.org/dl.php?id=3", ".mp3"),
    ],
)
def test_audio_suffix_prefers_the_declared_type_then_the_url(mime, url, expected):
    from mashup.feed.parse import Episode

    episode = Episode(
        guid="g",
        guid_source="guid",
        title="t",
        enclosure_url=url,
        declared_enclosure_url=url,
        enclosure_type=mime,
    )
    assert audio_suffix(episode) == expected


# --------------------------------------------------------------------------
# acquisition
# --------------------------------------------------------------------------


def acquire_newest(tmp_path, fetcher, **kwargs):
    feed = resolve_feed(KETTLE_URL, fetcher=fetcher)
    return feed, acquire_episode(
        feed,
        feed.episodes[0],
        cache_dir=tmp_path,
        fetcher=fetcher,
        license_url=check_rights(feed),
        **kwargs,
    )


def test_acquisition_writes_the_audio_and_a_provenance_record(tmp_path):
    fetcher = kettle_fetcher()
    feed, got = acquire_newest(tmp_path, fetcher)

    assert got.cached is False
    assert got.audio_path.read_bytes() == AUDIO
    assert got.audio_path.name == "audio.mp3"
    assert not list(got.audio_path.parent.glob("*.part"))

    record = json.loads(got.record_path.read_text())
    assert record["schema"] == ACQUISITION_SCHEMA
    assert record["feed"]["url"] == KETTLE_URL
    assert record["episode"]["guid"] == "kettle-0003"
    assert record["episode"]["durationSeconds"] == 3723.0
    assert record["enclosure"]["resolvedUrl"] == "https://cdn.example.org/kettle/003.mp3"
    assert record["rights"]["license"] == "https://creativecommons.org/licenses/by/4.0/"
    assert record["rights"]["override"] is False
    assert record["audio"]["sha256"] == sha256_file(got.audio_path)
    assert record["audio"]["bytes"] == len(AUDIO)
    assert record["fetchedAt"]


def test_record_notes_when_the_declared_length_was_wrong(tmp_path):
    """`length` attributes are stale in the wild, so this is recorded, not
    fatal — but it is recorded."""
    _, got = acquire_newest(tmp_path, kettle_fetcher())
    assert got.record["enclosure"]["declaredBytes"] == 49766400
    assert got.record["enclosure"]["declaredBytesMatch"] is False


def test_rerun_reuses_the_cached_audio_without_touching_the_network(tmp_path):
    first_fetcher = kettle_fetcher()
    _, first = acquire_newest(tmp_path, first_fetcher)

    second_fetcher = kettle_fetcher()
    feed = resolve_feed(KETTLE_URL, fetcher=second_fetcher)
    second = acquire_episode(
        feed,
        feed.episodes[0],
        cache_dir=tmp_path,
        fetcher=second_fetcher,
        license_url=check_rights(feed),
    )

    assert second.cached is True
    assert second.audio_path == first.audio_path
    assert second.sha256 == first.sha256
    assert second.record["fetchedAt"] == first.record["fetchedAt"]
    # The feed itself is re-read; the audio is not.
    assert second_fetcher.calls == [("get", KETTLE_URL)]


def test_a_corrupted_cached_file_is_downloaded_again(tmp_path):
    _, first = acquire_newest(tmp_path, kettle_fetcher())
    first.audio_path.write_bytes(b"truncated")

    fetcher = kettle_fetcher()
    _, second = acquire_newest(tmp_path, fetcher)
    assert second.cached is False
    assert second.audio_path.read_bytes() == AUDIO
    assert ("stream", "https://cdn.example.org/kettle/003.mp3", 0) in fetcher.calls


def test_a_missing_record_forces_a_refetch_even_though_the_audio_is_there(tmp_path):
    _, first = acquire_newest(tmp_path, kettle_fetcher())
    first.record_path.unlink()
    _, second = acquire_newest(tmp_path, kettle_fetcher())
    assert second.cached is False
    assert second.record_path.is_file()


def test_an_interrupted_download_resumes_from_the_partial_file(tmp_path):
    feed = resolve_feed(KETTLE_URL, fetcher=kettle_fetcher())
    episode = feed.episodes[0]
    directory = episode_dir(tmp_path, feed.url, episode)
    directory.mkdir(parents=True)
    (directory / "audio.mp3.part").write_bytes(AUDIO[:1000])

    fetcher = kettle_fetcher()
    got = acquire_episode(
        feed,
        episode,
        cache_dir=tmp_path,
        fetcher=fetcher,
        license_url=check_rights(feed),
    )
    assert ("stream", "https://cdn.example.org/kettle/003.mp3", 1000) in fetcher.calls
    assert got.audio_path.read_bytes() == AUDIO


def test_a_server_that_ignores_range_restarts_instead_of_corrupting(tmp_path):
    feed = resolve_feed(KETTLE_URL, fetcher=kettle_fetcher())
    episode = feed.episodes[0]
    directory = episode_dir(tmp_path, feed.url, episode)
    directory.mkdir(parents=True)
    (directory / "audio.mp3.part").write_bytes(AUDIO[:1000])

    got = acquire_episode(
        feed,
        episode,
        cache_dir=tmp_path,
        fetcher=kettle_fetcher(ignore_range=True),
        license_url=check_rights(feed),
    )
    assert got.audio_path.read_bytes() == AUDIO


def test_a_failed_download_keeps_the_partial_file_and_raises(tmp_path):
    url = "https://cdn.example.org/kettle/003.mp3"
    fetcher = kettle_fetcher(errors={url: OSError("connection reset")})
    feed = resolve_feed(KETTLE_URL, fetcher=fetcher)
    with pytest.raises(AcquireError, match="download failed"):
        acquire_episode(
            feed,
            feed.episodes[0],
            cache_dir=tmp_path,
            fetcher=fetcher,
            license_url=check_rights(feed),
        )
    assert not (episode_dir(tmp_path, feed.url, feed.episodes[0]) / "audio.mp3").exists()


def test_an_empty_enclosure_is_an_error_not_a_zero_byte_episode(tmp_path):
    fetcher = kettle_fetcher()
    fetcher.documents["https://cdn.example.org/kettle/003.mp3"] = b""
    feed = resolve_feed(KETTLE_URL, fetcher=fetcher)
    with pytest.raises(AcquireError, match="no audio"):
        acquire_episode(
            feed,
            feed.episodes[0],
            cache_dir=tmp_path,
            fetcher=fetcher,
            license_url=check_rights(feed),
        )


def test_two_episodes_of_one_feed_do_not_share_a_cache_directory(tmp_path):
    fetcher = kettle_fetcher()
    feed = resolve_feed(KETTLE_URL, fetcher=fetcher)
    paths = {
        acquire_episode(
            feed,
            episode,
            cache_dir=tmp_path,
            fetcher=fetcher,
            license_url=check_rights(feed),
        ).audio_path
        for episode in feed.episodes
    }
    assert len(paths) == 3
