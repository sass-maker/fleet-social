from __future__ import annotations

import math

import pytest

from mashup.models import Cue, Role, Segment, SegmentMeta


def make_cues(spans: list[tuple[float, float, str]], speaker: str | None = None) -> list[Cue]:
    return [
        Cue(index=i, start=s, end=e, text=t, speaker=speaker) for i, (s, e, t) in enumerate(spans)
    ]


def unit_vec(angle: float, dim: int = 8) -> list[float]:
    """A deterministic unit vector. Two vectors at a small angle are similar,
    which lets scoring tests assert on similarity without a real embedder."""
    vec = [0.0] * dim
    vec[0] = math.cos(angle)
    vec[1] = math.sin(angle)
    return vec


def make_segment(
    seg_id: str,
    *,
    source_id: str = "ep01",
    start: float = 0.0,
    duration: float = 60.0,
    angle: float = 0.0,
    role: Role = Role.DEVELOPMENT,
    energy: float = 0.5,
    topics: list[str] | None = None,
    entities: list[str] | None = None,
    required_context: list[str] | None = None,
    can_open: bool = False,
    can_end: bool = False,
    text: str = "some material",
) -> Segment:
    return Segment(
        id=seg_id,
        source_id=source_id,
        start=start,
        end=start + duration,
        text=text,
        cue_start=0,
        cue_end=1,
        meta=SegmentMeta(
            topic=topics or ["parents"],
            role=role,
            summary=f"summary for {seg_id}",
            required_context=required_context or [],
            energy=energy,
            can_open=can_open,
            can_end=can_end,
            entities=entities or [],
        ),
        embedding=unit_vec(angle),
    )


@pytest.fixture
def cosine_sim():
    def sim(a: Segment, b: Segment) -> float:
        va, vb = a.embedding or [], b.embedding or []
        dot = sum(x * y for x, y in zip(va, vb, strict=False))
        na = math.sqrt(sum(x * x for x in va)) or 1e-8
        nb = math.sqrt(sum(x * x for x in vb)) or 1e-8
        return dot / (na * nb)

    return sim


# --------------------------------------------------------------------------
# feed acquisition doubles
#
# The acquisition path reaches the network through one protocol, so the whole
# of it — pagination, caching, resume, provenance — is exercised against these
# instead. `no_network` is the proof rather than the promise.
# --------------------------------------------------------------------------


class StubFetcher:
    """An offline `mashup.feed.Fetcher` serving bytes from a dict.

    Honours `Range` (so resume is testable), can be told to ignore it (so the
    restart path is testable), follows configured redirects (so the
    post-redirect base URL is testable), and records every call it was asked
    to make (so "the rerun did not hit the network" is an assertion, not a
    hope).
    """

    def __init__(
        self,
        documents: dict[str, bytes],
        *,
        redirects: dict[str, str] | None = None,
        errors: dict[str, Exception] | None = None,
        ignore_range: bool = False,
    ) -> None:
        self.documents = dict(documents)
        self.redirects = dict(redirects or {})
        self.errors = dict(errors or {})
        self.ignore_range = ignore_range
        self.calls: list[tuple] = []

    # -- helpers ----------------------------------------------------------

    def final_url(self, url: str) -> str:
        seen: set[str] = set()
        while url in self.redirects and url not in seen:
            seen.add(url)
            url = self.redirects[url]
        return url

    def _body(self, url: str) -> bytes:
        if url in self.errors:
            raise self.errors[url]
        if url not in self.documents:
            raise AssertionError(f"stub fetcher has nothing at {url}")
        return self.documents[url]

    @property
    def urls(self) -> list[str]:
        return [call[1] for call in self.calls]

    # -- the protocol -----------------------------------------------------

    def get(self, url: str):
        from mashup.feed.acquire import Response

        self.calls.append(("get", url))
        final = self.final_url(url)
        return Response(url=final, content=self._body(final), content_type="application/rss+xml")

    def stream(self, url: str, dest, *, resume_from: int = 0):
        from pathlib import Path

        from mashup.feed.acquire import Download

        self.calls.append(("stream", url, resume_from))
        final = self.final_url(url)
        body = self._body(final)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if resume_from and not self.ignore_range:
            with dest.open("ab") as handle:
                handle.write(body[resume_from:])
            written = resume_from + len(body[resume_from:])
        else:
            dest.write_bytes(body)
            written = len(body)
        return Download(url=final, total_bytes=written, content_type="audio/mpeg")

    def close(self) -> None:
        self.calls.append(("close", None))


@pytest.fixture
def no_network(monkeypatch):
    """Turn any attempt to open a socket into a test failure."""
    import socket

    def deny(*_args, **_kwargs):
        raise AssertionError("this test tried to open a network connection")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
