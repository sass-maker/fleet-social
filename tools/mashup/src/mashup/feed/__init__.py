"""Podcast RSS acquisition: feed URL -> cached, provenance-backed audio.

The stage that runs before `mashup ingest`. It resolves a feed, lists its
episodes, and caches one episode's audio with a record of where it came from.
It stops there: transcription remains the existing WhisperKit/MLX path, and
nothing here synthesises audio.
"""

from __future__ import annotations

from mashup.feed.acquire import (
    ACQUISITION_SCHEMA,
    AcquireError,
    Acquisition,
    Download,
    Fetcher,
    HttpFetcher,
    Response,
    RightsError,
    acquire_episode,
    check_rights,
    resolve_feed,
)
from mashup.feed.parse import Dropped, Episode, Feed, FeedError, parse_feed

__all__ = [
    "ACQUISITION_SCHEMA",
    "AcquireError",
    "Acquisition",
    "Download",
    "Dropped",
    "Episode",
    "Feed",
    "FeedError",
    "Fetcher",
    "HttpFetcher",
    "Response",
    "RightsError",
    "acquire_episode",
    "check_rights",
    "parse_feed",
    "resolve_feed",
]
