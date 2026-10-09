"""Offline tests for podcast feed parsing — committed fixtures, no network.

`tests/feeds/adversarial.xml` is the interesting one: it carries every defect
this parser claims to survive, so a regression there is a regression in the
claim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mashup.feed.parse import Feed, FeedError, merge_pages, parse_duration, parse_feed

FEEDS = Path(__file__).resolve().parent / "feeds"

KETTLE_URL = "https://feeds.example.org/kettle/rss"
BROKEN_URL = "https://feeds.example.org/broken/rss"
PAGED_URL = "https://feeds.example.org/paged/rss"


def load(name: str, url: str) -> Feed:
    return parse_feed((FEEDS / f"{name}.xml").read_bytes(), feed_url=url)


@pytest.fixture(scope="module")
def kettle() -> Feed:
    return load("basic", KETTLE_URL)


@pytest.fixture(scope="module")
def broken() -> Feed:
    return load("adversarial", BROKEN_URL)


# --------------------------------------------------------------------------
# durations
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1:02:03", 3723.0),
        ("01:02:03", 3723.0),
        ("45:30", 2730.0),
        ("3600", 3600.0),
        ("3600.5", 3600.5),
        ("0:42", 42.0),
    ],
)
def test_itunes_duration_accepts_every_form_publishers_use(raw, expected):
    assert parse_duration(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", None, "approximately one hour", "1:02:03:04", "-90", "one hour", "0", "00:00"],
)
def test_itunes_duration_returns_none_rather_than_guessing(raw):
    """A wrong runtime is worse than a blank one; it is shown to a human."""
    assert parse_duration(raw) is None


# --------------------------------------------------------------------------
# a clean feed
# --------------------------------------------------------------------------


def test_clean_feed_lists_every_episode_in_document_order(kettle):
    assert kettle.title == "The Kettle Logic Show"
    assert [e.guid for e in kettle.episodes] == ["kettle-0003", "kettle-0002", "kettle-0001"]
    assert kettle.dropped == ()
    assert kettle.parse_warning is None
    assert kettle.pages == 1


def test_clean_feed_normalises_the_fields_selection_needs(kettle):
    newest = kettle.episodes[0]
    assert newest.title == "Episode 3 — The Long One"
    assert newest.published == "2025-03-12T09:00:00+00:00"
    assert newest.duration == 3723.0
    assert newest.enclosure_url == "https://cdn.example.org/kettle/003.mp3"
    assert newest.enclosure_type == "audio/mpeg"
    assert newest.enclosure_bytes == 49766400
    assert newest.guid_source == "guid"
    assert not newest.guid_is_borrowed


def test_licence_is_read_from_the_atom_link(kettle):
    assert kettle.license_urls == ("https://creativecommons.org/licenses/by/4.0/",)


def test_find_looks_an_episode_up_by_guid(kettle):
    assert kettle.find("kettle-0002").title.startswith("Episode 2")
    assert kettle.find("nope") is None


# --------------------------------------------------------------------------
# the adversarial feed
# --------------------------------------------------------------------------


def test_duplicate_guid_keeps_the_first_occurrence(broken):
    """Later wins would change what an already-cached key means between runs."""
    first = broken.find("broken-1")
    assert first.title == "Sound & Fury"
    assert sum(e.guid == "broken-1" for e in broken.episodes) == 1
    assert any("duplicate guid 'broken-1'" in d.reason for d in broken.dropped)


def test_entry_without_an_enclosure_is_dropped_with_a_reason(broken):
    dropped = next(d for d in broken.dropped if d.title == "Show notes only")
    assert dropped.reason == "no enclosure"
    assert broken.find("broken-3") is None


def test_relative_enclosure_resolves_against_the_feed_url(broken):
    episode = next(e for e in broken.episodes if e.title == "Relative and undated")
    assert episode.declared_enclosure_url == "/audio/relative.mp3"
    assert episode.enclosure_url == "https://feeds.example.org/audio/relative.mp3"


def test_entry_without_a_guid_falls_back_to_its_enclosure(broken):
    episode = next(e for e in broken.episodes if e.title == "Relative and undated")
    assert episode.guid_source == "enclosure"
    assert episode.guid == episode.enclosure_url


def test_unparseable_date_and_duration_become_none_not_zero(broken):
    episode = next(e for e in broken.episodes if e.title == "Relative and undated")
    assert episode.published is None
    assert broken.find("broken-1").duration is None


def test_non_http_enclosure_never_becomes_a_fetchable_url(broken):
    """A `javascript:` enclosure must not survive as something to GET."""
    dropped = next(d for d in broken.dropped if d.title == "Hostile enclosure")
    assert "unfetchable enclosure URL" in dropped.reason
    assert all(e.enclosure_url.startswith("https://") for e in broken.episodes)


def test_untitled_entry_still_gets_a_usable_label(broken):
    assert broken.find("broken-6").title == "Untitled episode 6"


def test_borrowed_guid_is_flagged_so_the_cache_key_is_not_mistaken_for_identity(broken):
    episode = next(e for e in broken.episodes if e.title == "Relative and undated")
    assert episode.guid_is_borrowed
    assert not broken.find("broken-1").guid_is_borrowed


def test_guid_falls_back_to_the_episode_link_before_the_audio_url():
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
    <item><title>Nameless</title><link>https://feeds.example.org/x/1</link>
    <enclosure url="https://cdn.example.org/x.mp3" type="audio/mpeg"/></item>
    </channel></rss>"""
    feed = parse_feed(xml, feed_url="https://feeds.example.org/x/rss")
    assert feed.episodes[0].guid == "https://feeds.example.org/x/1"
    assert feed.episodes[0].guid_source == "link"


# --------------------------------------------------------------------------
# malformed documents
# --------------------------------------------------------------------------


def test_malformed_xml_is_parsed_as_far_as_it_reads_and_says_so():
    feed = load("malformed", "https://feeds.example.org/salt/rss")
    assert feed.parse_warning is not None
    assert [e.guid for e in feed.episodes] == ["salt-1", "salt-2"]
    assert feed.episodes[0].title == "Fish & Chips"


def test_empty_document_is_an_error_not_an_empty_feed():
    with pytest.raises(FeedError):
        parse_feed(b"   ", feed_url="https://feeds.example.org/nothing")


def test_html_page_is_an_error_not_an_empty_feed():
    with pytest.raises(FeedError):
        parse_feed(b"not xml at all", feed_url="https://feeds.example.org/nothing")


# --------------------------------------------------------------------------
# pagination
# --------------------------------------------------------------------------


def test_next_link_is_resolved_to_an_absolute_url():
    assert load("page1", PAGED_URL).next_url == "https://feeds.example.org/paged/rss?page=2"


def test_merge_pages_deduplicates_the_repeated_boundary_episode():
    merged = merge_pages([load("page1", PAGED_URL), load("page2", PAGED_URL + "?page=2")])
    assert [e.guid for e in merged.episodes] == ["paged-3", "paged-2", "paged-1"]
    assert merged.pages == 2
    assert merged.title == "Paginated Pod"
    assert any("page 2" in d.reason for d in merged.dropped)


def test_merge_pages_of_one_is_the_page_itself():
    page = load("page1", PAGED_URL)
    assert merge_pages([page]) is page


def test_merge_pages_of_nothing_is_an_error():
    with pytest.raises(FeedError):
        merge_pages([])
