"""Feed URL -> a cached, provenance-backed local audio file.

Everything that touches the network goes through the `Fetcher` protocol, so
the whole acquisition path can be exercised offline against committed feed
fixtures and a stub. `HttpFetcher` is the one implementation that opens a
socket.

Two rules shape the design:

- **Fail closed on rights.** Mashup only edits creator-owned, appropriately
  licensed, or public-domain material, and a podcast feed usually says
  nothing machine-readable about its licence. So an unrecognised licence
  refuses the download and points at the same `--i-have-rights` escape hatch
  `scripts/fetch_archive.py` uses for a creator fetching their own material.
- **A rerun costs nothing.** The cache is keyed on (feed URL, episode GUID)
  and validated by content hash, so a second run re-reads the acquisition
  record instead of the network.

This module stops at a downloaded file. It does not transcribe: the audio
lands somewhere `mashup ingest` can pick it up, and the existing
WhisperKit/MLX path takes it from there unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from mashup.feed.parse import Episode, Feed, FeedError, merge_pages, parse_feed

ACQUISITION_SCHEMA = "fleet.mashup-episode-acquisition.v1"
TOOL = "mashup-feed/0.1"
USER_AGENT = f"{TOOL} (+https://github.com/sass-maker/mashup; podcast episode fetcher)"
CHUNK = 1 << 20
DEFAULT_MAX_PAGES = 10
# Feed documents are small; a publisher serving hundreds of megabytes of XML
# is a mistake or an attack, and either way not something to buffer.
MAX_FEED_BYTES = 32 << 20

# Extensions `mashup ingest` already knows how to open, keyed by the MIME type
# publishers actually set. Anything unrecognised keeps the URL's own suffix.
_AUDIO_SUFFIX = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/aac": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/vnd.wave": ".wav",
    "video/mp4": ".mp4",
}
INGESTABLE_SUFFIXES = frozenset({".mp3", ".m4a", ".wav", ".mp4", ".mkv", ".mov"})
# Extensions worth trusting from an enclosure URL when the MIME type is
# unhelpful. Anything else — `.bin`, `.php`, a tracking path with no suffix —
# becomes `.mp3`, which is what an `application/octet-stream` podcast
# enclosure almost always is; the declared type and URL stay in the record.
_KNOWN_MEDIA_SUFFIXES = INGESTABLE_SUFFIXES | {
    ".aac",
    ".flac",
    ".m4b",
    ".oga",
    ".ogg",
    ".opus",
    ".webm",
}
_SLUG = re.compile(r"[^a-z0-9]+")


class AcquireError(RuntimeError):
    """Raised when an episode could not be fetched or cached."""


class RightsError(RuntimeError):
    """Raised when the feed's licence does not clearly permit derivatives."""


# --------------------------------------------------------------------------
# the network boundary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    url: str  # after redirects
    content: bytes
    content_type: str | None = None


@dataclass(frozen=True)
class Download:
    url: str  # after redirects
    total_bytes: int  # size of the file on disk once the call returns
    content_type: str | None = None


class Fetcher(Protocol):
    """The only thing in this module that is allowed to reach the network."""

    def get(self, url: str) -> Response: ...

    def stream(self, url: str, dest: Path, *, resume_from: int = 0) -> Download: ...


class HttpFetcher:
    """httpx, with redirects followed so the final URL is recorded."""

    def __init__(self, *, timeout: float = 60.0) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=timeout
        )

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, url: str) -> Response:
        response = self._client.get(url)
        response.raise_for_status()
        if len(response.content) > MAX_FEED_BYTES:
            raise AcquireError(f"feed document at {url} exceeds {MAX_FEED_BYTES} bytes")
        return Response(
            url=str(response.url),
            content=response.content,
            content_type=response.headers.get("content-type"),
        )

    def stream(self, url: str, dest: Path, *, resume_from: int = 0) -> Download:
        headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
        written = resume_from
        mode = "ab" if resume_from else "wb"
        with self._client.stream("GET", url, headers=headers) as response:
            if resume_from and response.status_code == 200:
                # Range ignored. Restart cleanly rather than concatenate two
                # copies of the first bytes onto the partial file.
                mode, written = "wb", 0
            response.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open(mode) as handle:
                for chunk in response.iter_bytes(CHUNK):
                    handle.write(chunk)
                    written += len(chunk)
            return Download(
                url=str(response.url),
                total_bytes=written,
                content_type=response.headers.get("content-type"),
            )


# --------------------------------------------------------------------------
# rights
# --------------------------------------------------------------------------


def check_rights(feed: Feed, *, override: bool = False) -> str:
    """Return the licence this acquisition relies on, or refuse.

    Mirrors `scripts/fetch_archive.py::check_license`: public-domain marks and
    CC licences without an `-nd` term are fine, an `-nd` licence is refused
    outright, and anything unreadable is refused with an override available
    for a creator fetching their own feed.
    """
    if override:
        declared = feed.license_urls[0] if feed.license_urls else ""
        return declared or "unspecified (--i-have-rights)"
    if not feed.license_urls:
        detail = f" (copyright notice: {feed.rights_text!r})" if feed.rights_text else ""
        raise RightsError(
            f"no machine-readable licence in {feed.url}{detail}; refusing to fetch. "
            "Use --i-have-rights only for a feed you own or have cleared."
        )
    refused: list[str] = []
    for url in feed.license_urls:
        low = url.lower()
        if "-nd" in low or "/nd/" in low:
            refused.append(url)
            continue
        if "publicdomain" in low or "creativecommons.org/licenses/" in low:
            return url
        refused.append(url)
    raise RightsError(
        "licence does not clearly permit derivative works: "
        + ", ".join(refused)
        + ". Use --i-have-rights only for a feed you own or have cleared."
    )


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def resolve_feed(feed_url: str, *, fetcher: Fetcher, max_pages: int = DEFAULT_MAX_PAGES) -> Feed:
    """Fetch a feed and follow `rel="next"` up to `max_pages` documents.

    Each page is parsed against its own post-redirect URL, and a `next` link
    pointing at a page already seen ends the walk — a self-referential
    `rel="next"` is a real and otherwise unbounded failure mode.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    pages: list[Feed] = []
    seen: set[str] = set()
    url: str | None = feed_url
    while url and len(pages) < max_pages:
        if url in seen:
            break
        seen.add(url)
        response = fetcher.get(url)
        page = parse_feed(response.content, feed_url=response.url)
        seen.add(response.url)
        pages.append(page)
        url = page.next_url
    if not pages:  # pragma: no cover - unreachable while max_pages >= 1
        raise FeedError(f"no pages fetched for {feed_url}")
    return merge_pages(pages)


# --------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_key(feed_url: str, guid: str) -> str:
    """Stable, readable, and collision-safe across feeds sharing a GUID."""
    digest = hashlib.sha256(f"{feed_url}\0{guid}".encode()).hexdigest()
    return digest[:16]


def episode_dir(cache_dir: Path, feed_url: str, episode: Episode) -> Path:
    slug = _SLUG.sub("-", episode.title.lower()).strip("-")[:48].strip("-") or "episode"
    return Path(cache_dir) / "episodes" / f"{slug}-{cache_key(feed_url, episode.guid)}"


def audio_suffix(episode: Episode) -> str:
    declared = (episode.enclosure_type or "").split(";")[0].strip().lower()
    if declared in _AUDIO_SUFFIX:
        return _AUDIO_SUFFIX[declared]
    suffix = Path(urlsplit(episode.enclosure_url).path).suffix.lower()
    return suffix if suffix in _KNOWN_MEDIA_SUFFIXES else ".mp3"


@dataclass(frozen=True)
class Acquisition:
    episode: Episode
    audio_path: Path
    record_path: Path
    record: dict[str, Any]
    cached: bool

    @property
    def sha256(self) -> str:
        return str(self.record["audio"]["sha256"])

    @property
    def size_bytes(self) -> int:
        return int(self.record["audio"]["bytes"])


def load_record(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _cache_hit(record_path: Path, audio_path: Path) -> dict[str, Any] | None:
    """A hit needs a record, the file it describes, and a matching hash.

    Size is checked first because it rejects a truncated download without
    reading the file, but the hash is what makes the record trustworthy.
    """
    if not record_path.is_file() or not audio_path.is_file():
        return None
    record = load_record(record_path)
    audio = record.get("audio") if isinstance(record, dict) else None
    if not isinstance(audio, dict) or record.get("schema") != ACQUISITION_SCHEMA:
        return None
    if audio.get("bytes") != audio_path.stat().st_size:
        return None
    if audio.get("sha256") != sha256_file(audio_path):
        return None
    return record


def build_record(
    feed: Feed,
    episode: Episode,
    *,
    license_url: str,
    rights_override: bool,
    audio_path: Path,
    final_url: str,
    digest: str,
    size: int,
    fetched_at: str,
) -> dict[str, Any]:
    return {
        "schema": ACQUISITION_SCHEMA,
        "fetchedAt": fetched_at,
        "fetchedBy": TOOL,
        "feed": {
            "url": feed.url,
            "title": feed.title,
            "link": feed.link,
            "pages": feed.pages,
        },
        "rights": {
            "license": license_url,
            "licenseUrls": list(feed.license_urls),
            "copyright": feed.rights_text,
            "override": rights_override,
        },
        "episode": {
            "guid": episode.guid,
            "guidSource": episode.guid_source,
            "title": episode.title,
            "link": episode.link,
            "published": episode.published,
            "durationSeconds": episode.duration,
        },
        "enclosure": {
            "declaredUrl": episode.declared_enclosure_url,
            "resolvedUrl": episode.enclosure_url,
            "finalUrl": final_url,
            "type": episode.enclosure_type,
            "declaredBytes": episode.enclosure_bytes,
            "declaredBytesMatch": (
                None if episode.enclosure_bytes is None else episode.enclosure_bytes == size
            ),
        },
        "audio": {
            "path": str(audio_path),
            "bytes": size,
            "sha256": digest,
        },
    }


def save_record(record: dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def acquire_episode(
    feed: Feed,
    episode: Episode,
    *,
    cache_dir: Path,
    fetcher: Fetcher,
    license_url: str,
    rights_override: bool = False,
    now: str | None = None,
) -> Acquisition:
    """Download one episode into the cache, or confirm it is already there.

    The download goes to a `.part` file that is resumed on a retry and only
    renamed into place once complete, so an interrupted run never leaves a
    truncated file that the next run would treat as the episode.
    """
    target_dir = episode_dir(cache_dir, feed.url, episode)
    audio_path = target_dir / f"audio{audio_suffix(episode)}"
    record_path = target_dir / "acquisition.json"

    hit = _cache_hit(record_path, audio_path)
    if hit is not None:
        return Acquisition(
            episode=episode,
            audio_path=audio_path,
            record_path=record_path,
            record=hit,
            cached=True,
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    part = audio_path.with_suffix(audio_path.suffix + ".part")
    resume_from = part.stat().st_size if part.is_file() else 0
    try:
        download = fetcher.stream(episode.enclosure_url, part, resume_from=resume_from)
    except Exception as exc:  # the partial file is kept for the next attempt
        raise AcquireError(f"{episode.title}: download failed: {exc}") from exc

    if not part.is_file() or download.total_bytes == 0:
        part.unlink(missing_ok=True)
        raise AcquireError(f"{episode.title}: enclosure returned no audio")
    part.replace(audio_path)

    size = audio_path.stat().st_size
    record = build_record(
        feed,
        episode,
        license_url=license_url,
        rights_override=rights_override,
        audio_path=audio_path,
        final_url=download.url,
        digest=sha256_file(audio_path),
        size=size,
        fetched_at=now or datetime.now(UTC).isoformat(),
    )
    save_record(record, record_path)
    return Acquisition(
        episode=episode,
        audio_path=audio_path,
        record_path=record_path,
        record=record,
        cached=False,
    )
