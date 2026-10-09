"""Podcast RSS/Atom document -> a normalised, fetchable episode list.

Pure. Nothing here opens a socket: callers hand in the bytes of a feed
document plus the URL those bytes came from, and get back episodes whose
enclosure URLs are already absolute.

Feeds in the wild are messy, so the parser is deliberately lossy in one
direction only — an entry it cannot turn into a fetchable episode is
*dropped with a stated reason* rather than half-kept. `Feed.dropped` is part
of the output so the CLI can show what was skipped instead of silently
listing a shorter feed than the publisher wrote.

`feedparser` does the XML and namespace work (see the PR/README note on why
it is a dependency); everything opinionated — enclosure choice, GUID
fallbacks, URL resolution, duration normalisation, de-duplication — lives
here where it is testable against committed fixtures.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import feedparser

# Anything else (`file:`, `javascript:`, `data:`) is not something this tool
# will hand to an HTTP client on the strength of a URL a stranger published.
ALLOWED_SCHEMES = frozenset({"http", "https"})

GuidSource = Literal["guid", "link", "enclosure"]

# feedparser hands back `FeedParserDict`s whose useful keys — `enclosures`,
# `rights`, `license` — are computed aliases rather than stored entries, so
# these mappings are read as-is and never copied into a plain dict.
Entry = Mapping[str, Any]

_HTTP_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_DURATION_CLOCK = re.compile(r"^\d+(:\d{1,2})*(\.\d+)?$")


class FeedError(RuntimeError):
    """Raised when a document cannot be read as a podcast feed at all."""


@dataclass(frozen=True)
class Episode:
    """One fetchable episode. Every field is already normalised."""

    guid: str
    guid_source: GuidSource
    title: str
    enclosure_url: str
    declared_enclosure_url: str
    enclosure_type: str | None = None
    enclosure_bytes: int | None = None
    published: str | None = None  # ISO-8601 UTC, or None if unparseable
    duration: float | None = None  # seconds, or None if absent/unparseable
    link: str | None = None

    @property
    def guid_is_borrowed(self) -> bool:
        """True when the feed published no `<guid>` and the id had to be
        borrowed from the episode's link or audio URL. Stable enough to cache
        on, but it changes if the publisher moves the file."""
        return self.guid_source != "guid"


@dataclass(frozen=True)
class Dropped:
    """An entry that could not become an `Episode`, and why."""

    index: int
    title: str
    reason: str


@dataclass(frozen=True)
class Feed:
    url: str
    title: str
    episodes: tuple[Episode, ...] = ()
    dropped: tuple[Dropped, ...] = ()
    link: str | None = None
    next_url: str | None = None
    license_urls: tuple[str, ...] = ()
    rights_text: str | None = None
    parse_warning: str | None = None
    pages: int = 1

    def find(self, guid: str) -> Episode | None:
        return next((e for e in self.episodes if e.guid == guid), None)


# --------------------------------------------------------------------------
# scalars
# --------------------------------------------------------------------------


def parse_duration(value: Any) -> float | None:
    """`<itunes:duration>` in any of the three forms publishers actually use.

    `3723`, `62:03` and `01:02:03` all mean the same hour. Anything else —
    "approx. 1 hour", an empty tag, a negative number — is not worth
    guessing at, and returns None so the caller shows a blank rather than a
    wrong runtime.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or not _DURATION_CLOCK.match(text):
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    try:
        numbers = [float(p) for p in parts]
    except ValueError:  # pragma: no cover - the regex already guarantees this
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds if seconds > 0 else None


def _published(entry: Entry) -> str | None:
    """feedparser normalises the RFC-822/ISO date zoo to UTC struct_time.

    When it cannot, it leaves `published_parsed` unset rather than guessing,
    and so do we: a wrong date would sort an episode list wrongly.
    """
    parsed = entry.get("published_parsed")
    if not parsed and "updated_parsed" in entry:
        # `in` rather than `get`: asking a feedparser entry for a date it does
        # not have triggers its deprecated published/updated fallback.
        parsed = entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=UTC).isoformat()
    except (TypeError, ValueError):
        return None


def _absolute(url: str, base: str) -> str | None:
    """Resolve against the feed URL and reject anything not fetchable."""
    candidate = urljoin(base, (url or "").strip())
    if urlsplit(candidate).scheme.lower() not in ALLOWED_SCHEMES:
        return None
    return candidate


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# --------------------------------------------------------------------------
# entries
# --------------------------------------------------------------------------


def _pick_enclosure(entry: Entry) -> Mapping[str, Any] | None:
    """Prefer a declared audio enclosure; fall back to the first with a URL.

    Video podcasts and feeds that mislabel `audio/mpeg` as
    `application/octet-stream` are both common, so a missing or odd type is
    not on its own a reason to skip an episode.
    """
    raw = entry.get("enclosures") or []
    enclosures = [e for e in raw if isinstance(e, dict) and e.get("href")]
    if not enclosures:
        return None
    audio = [e for e in enclosures if str(e.get("type") or "").lower().startswith("audio/")]
    return (audio or enclosures)[0]


def _guid(entry: Entry, *, enclosure_url: str) -> tuple[str, GuidSource]:
    """The cache key, and which rung of the fallback chain produced it.

    Plenty of feeds omit `<guid>`. A borrowed id still caches correctly, but
    it is the publisher's URL rather than the publisher's identity for the
    episode, so the acquisition record says which one it was instead of
    presenting a guess as the feed's own answer.
    """
    raw = (entry.get("id") or "").strip()
    if raw:
        return raw, "guid"
    link = (entry.get("link") or "").strip()
    if link:
        return link, "link"
    return enclosure_url, "enclosure"


def _episode(entry: Entry, *, index: int, base: str) -> Episode | Dropped:
    title = str(entry.get("title") or "").strip() or f"Untitled episode {index + 1}"
    enclosure = _pick_enclosure(entry)
    if enclosure is None:
        return Dropped(index=index, title=title, reason="no enclosure")

    declared = str(enclosure.get("href") or "").strip()
    resolved = _absolute(declared, base)
    if resolved is None:
        return Dropped(index=index, title=title, reason=f"unfetchable enclosure URL: {declared!r}")

    published = _published(entry)
    guid, guid_source = _guid(entry, enclosure_url=resolved)
    link = (entry.get("link") or "").strip() or None
    return Episode(
        guid=guid,
        guid_source=guid_source,
        title=title,
        enclosure_url=resolved,
        declared_enclosure_url=declared,
        enclosure_type=str(enclosure.get("type") or "").strip() or None,
        enclosure_bytes=_int_or_none(enclosure.get("length")),
        published=published,
        duration=parse_duration(entry.get("itunes_duration")),
        link=urljoin(base, link) if link else None,
    )


# --------------------------------------------------------------------------
# channel
# --------------------------------------------------------------------------


def _license_urls(channel: Entry) -> tuple[str, ...]:
    """Every machine-readable licence URL the channel offers.

    Podcast feeds have no single licence field. `<atom:link rel="license">`
    is the well-specified one; `<copyright>` prose containing a Creative
    Commons URL is the one publishers actually use.
    """
    found: list[str] = []
    for link in channel.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "license" and link.get("href"):
            found.append(str(link["href"]).strip())
    if channel.get("license"):
        found.append(str(channel["license"]).strip())
    for field in ("rights", "copyright"):
        found.extend(_HTTP_URL.findall(str(channel.get(field) or "")))
    seen: dict[str, None] = {}
    for url in found:
        if url:
            seen.setdefault(url, None)
    return tuple(seen)


def _next_url(channel: Entry, base: str) -> str | None:
    """RFC 5005 `rel="next"`, the only pagination signal worth trusting."""
    for link in channel.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "next" and link.get("href"):
            return _absolute(str(link["href"]), base)
    return None


def parse_feed(data: bytes, *, feed_url: str) -> Feed:
    """Parse one feed document. `feed_url` is the URL the bytes came from.

    Pass the URL *after* redirects: it is the base every relative enclosure
    and `rel="next"` link is resolved against, and a feed that has moved
    resolves its own relative URLs against where it now lives.
    """
    if not data or not data.strip():
        raise FeedError(f"empty document at {feed_url}")

    parsed = feedparser.parse(data)
    channel: Entry = parsed.get("feed") or {}
    entries: list[Entry] = list(parsed.get("entries") or [])
    warning = None
    if parsed.get("bozo"):
        warning = str(parsed.get("bozo_exception") or "malformed feed document")
    if not channel and not entries:
        raise FeedError(f"not a feed document: {feed_url} ({warning or 'no channel, no entries'})")

    episodes: list[Episode] = []
    dropped: list[Dropped] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        result = _episode(entry, index=index, base=feed_url)
        if isinstance(result, Dropped):
            dropped.append(result)
        elif result.guid in seen:
            # First occurrence wins. Republished-with-the-same-guid items are
            # common; taking the later one would change what a cached key
            # means between runs.
            dropped.append(
                Dropped(index=index, title=result.title, reason=f"duplicate guid {result.guid!r}")
            )
        else:
            seen.add(result.guid)
            episodes.append(result)

    return Feed(
        url=feed_url,
        title=str(channel.get("title") or "").strip() or feed_url,
        episodes=tuple(episodes),
        dropped=tuple(dropped),
        link=str(channel.get("link") or "").strip() or None,
        next_url=_next_url(channel, feed_url),
        license_urls=_license_urls(channel),
        rights_text=str(channel.get("rights") or "").strip() or None,
        parse_warning=warning,
    )


def merge_pages(pages: list[Feed]) -> Feed:
    """Fold a paginated feed into one, keeping the first page's identity.

    Publishers routinely repeat the boundary episode on the next page, so the
    same first-wins de-duplication applies across pages as within one.
    """
    if not pages:
        raise FeedError("no feed pages to merge")
    head = pages[0]
    if len(pages) == 1:
        return head

    episodes: list[Episode] = []
    dropped: list[Dropped] = list(head.dropped)
    seen: set[str] = set()
    for page_number, page in enumerate(pages, start=1):
        if page_number > 1:
            dropped.extend(page.dropped)
        for episode in page.episodes:
            if episode.guid in seen:
                dropped.append(
                    Dropped(
                        index=len(episodes),
                        title=episode.title,
                        reason=f"duplicate guid {episode.guid!r} (page {page_number})",
                    )
                )
                continue
            seen.add(episode.guid)
            episodes.append(episode)

    warning = next((p.parse_warning for p in pages if p.parse_warning), None)
    return replace(
        head,
        episodes=tuple(episodes),
        dropped=tuple(dropped),
        next_url=pages[-1].next_url,
        parse_warning=warning,
        pages=len(pages),
    )
