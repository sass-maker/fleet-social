#!/usr/bin/env python3
"""Fetch a public-domain / derivative-friendly archive.org item for local dev.

The mashup pipeline only ever operates on creator-owned or public-domain
material, so this fetcher refuses to run against a licence it cannot verify
and always writes a PROVENANCE.json next to the media.

Exit codes:
    0   success (or dry run)
    1   runtime/network error
    2   bad usage (argparse)
    3   licence refused
    4   checksum mismatch (corrupt file deleted)
    130 interrupted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

TOOL = "mashup-fetch-archive"
USER_AGENT = f"{TOOL}/0.1 (+https://github.com/sarthak-fleet/mashup; archive dev fetcher)"
METADATA_URL = "https://archive.org/metadata/{item}"
DOWNLOAD_URL = "https://archive.org/download/{item}/{name}"
CHUNK = 1 << 20

EXIT_ERROR = 1
EXIT_LICENSE = 3
EXIT_CHECKSUM = 4
EXIT_INTERRUPT = 130


class LicenseError(RuntimeError):
    """The item's licence does not permit derivative works."""


class ChecksumError(RuntimeError):
    """A downloaded file did not match the published md5."""


@dataclass(frozen=True)
class ArchiveFile:
    name: str
    size: int
    md5: str | None
    format: str

    @property
    def url_name(self) -> str:
        return quote(self.name)


@dataclass(frozen=True)
class DownloadPlan:
    file: ArchiveFile
    target: Path
    part: Path
    resume_from: int  # bytes already on disk in `part`
    done: bool  # already fully downloaded at `target`

    @property
    def remaining(self) -> int:
        return 0 if self.done else max(self.file.size - self.resume_from, 0)

    @property
    def range_header(self) -> dict[str, str]:
        """Range header for resuming; empty when starting from scratch."""
        return {"Range": f"bytes={self.resume_from}-"} if self.resume_from else {}


# --------------------------------------------------------------------------
# pure helpers (unit-tested offline)
# --------------------------------------------------------------------------


def _as_text(value: Any) -> str:
    """archive.org metadata fields are sometimes lists of strings."""
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return "" if value is None else str(value)


def check_license(metadata: dict[str, Any], *, override: bool = False) -> str:
    """Return the licence URL, raising LicenseError when derivatives are barred.

    Accepts public domain marks/CC0 and CC licences without an `-nd` term.
    `override` is the escape hatch for a creator fetching their own material.
    """
    licenseurl = _as_text(metadata.get("licenseurl")).strip()
    if override:
        return licenseurl or "unspecified (--i-have-rights)"

    low = licenseurl.lower()
    if not low:
        raise LicenseError(
            "no licenseurl in item metadata; refusing to fetch. "
            "Use --i-have-rights only for material you own."
        )
    if "-nd" in low or "/nd/" in low:
        raise LicenseError(f"licence forbids derivative works: {licenseurl}")
    if "publicdomain" in low or "creativecommons.org/licenses/" in low:
        return licenseurl
    raise LicenseError(f"unrecognised licence, cannot confirm derivatives allowed: {licenseurl}")


def select_files(
    files: list[dict[str, Any]], *, fmt: str = "MPEG4", limit: int | None = None
) -> list[ArchiveFile]:
    """Pick files of `fmt`, sorted by name, capped at `limit`."""
    picked = [
        ArchiveFile(
            name=f["name"],
            size=int(f.get("size") or 0),
            md5=(f.get("md5") or None),
            format=f.get("format", ""),
        )
        for f in files
        if f.get("format") == fmt and f.get("name")
    ]
    picked.sort(key=lambda f: f.name)
    return picked[:limit] if limit is not None else picked


def plan_downloads(files: list[ArchiveFile], dest: Path) -> list[DownloadPlan]:
    """Resolve each file against what is already on disk."""
    plans: list[DownloadPlan] = []
    for f in files:
        target = dest / f.name
        part = dest / f"{f.name}.part"
        done = target.exists() and (f.size == 0 or target.stat().st_size == f.size)
        resume = 0
        if not done and part.exists():
            resume = part.stat().st_size
            if f.size and resume > f.size:  # corrupt/stale partial
                resume = 0
        plans.append(DownloadPlan(file=f, target=target, part=part, resume_from=resume, done=done))
    return plans


def file_md5(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 - archive.org publishes md5, not a security boundary
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TiB"


def build_provenance(
    item: str, metadata: dict[str, Any], files: list[ArchiveFile], license_url: str
) -> dict[str, Any]:
    return {
        "item": item,
        "title": _as_text(metadata.get("title")) or item,
        "licenseurl": license_url,
        "source_url": f"https://archive.org/details/{item}",
        "metadata_url": METADATA_URL.format(item=item),
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "fetched_by": TOOL,
        "files": [{"name": f.name, "size": f.size, "md5": f.md5} for f in files],
    }


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------


def fetch_metadata(client: httpx.Client, item: str) -> dict[str, Any]:
    resp = client.get(METADATA_URL.format(item=item), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data or not data.get("files"):
        raise RuntimeError(f"no files in metadata for item {item!r}")
    return data


def download_one(client: httpx.Client, item: str, plan: DownloadPlan) -> None:
    """Stream one file to `.part`, verify, then rename. Safe to re-run."""
    f = plan.file
    url = DOWNLOAD_URL.format(item=item, name=f.url_name)
    headers = plan.range_header
    mode = "ab" if plan.resume_from else "wb"
    written = plan.resume_from

    with client.stream("GET", url, headers=headers, timeout=120) as resp:
        if plan.resume_from and resp.status_code == 200:
            # Server ignored Range; restart cleanly rather than corrupt the file.
            mode, written = "wb", 0
        elif plan.resume_from and resp.status_code != 206:
            resp.raise_for_status()
        resp.raise_for_status()
        with plan.part.open(mode) as fh:
            for chunk in resp.iter_bytes(CHUNK):
                fh.write(chunk)
                written += len(chunk)
                _progress(f.name, written, f.size)
    print(file=sys.stderr)

    if f.size and written != f.size:
        raise RuntimeError(f"{f.name}: expected {f.size} bytes, got {written} (partial kept)")
    if f.md5:
        actual = file_md5(plan.part)
        if actual != f.md5:
            plan.part.unlink(missing_ok=True)
            raise ChecksumError(f"{f.name}: md5 mismatch (expected {f.md5}, got {actual}); deleted")
    plan.part.replace(plan.target)


def _progress(name: str, done: int, total: int) -> None:
    pct = f"{done / total * 100:5.1f}%" if total else "  ?  "
    print(f"\r  {name}  {pct}  {human(done)}/{human(total)}", end="", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fetch_archive.py",
        description=(
            "Download a public-domain archive.org item for mashup development. "
            "Refuses licences that forbid derivative works and records PROVENANCE.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            "  python scripts/fetch_archive.py --item ybylcollection "
            "--dest ./archive --limit 20 --dry-run\n"
        ),
    )
    p.add_argument("--item", required=True, help="archive.org item identifier")
    p.add_argument("--dest", type=Path, required=True, help="destination directory")
    p.add_argument("--limit", type=int, default=None, help="max files to download")
    p.add_argument("--format", dest="fmt", default="MPEG4", help="archive.org format (MPEG4)")
    p.add_argument("--dry-run", action="store_true", help="list what would be fetched, then exit")
    p.add_argument("--sleep", type=float, default=1.0, help="seconds between files (default 1.0)")
    p.add_argument(
        "--i-have-rights",
        action="store_true",
        help="skip the licence gate; for creators fetching their own material",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dest: Path = args.dest

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        try:
            data = fetch_metadata(client, args.item)
        except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
            print(f"error: metadata fetch failed: {exc}", file=sys.stderr)
            return EXIT_ERROR

        metadata = data.get("metadata", {})
        try:
            license_url = check_license(metadata, override=args.i_have_rights)
        except LicenseError as exc:
            print(f"refusing: {exc}", file=sys.stderr)
            return EXIT_LICENSE
        print(f"licence: {license_url}", file=sys.stderr)

        files = select_files(data["files"], fmt=args.fmt, limit=args.limit)
        if not files:
            print(f"error: no {args.fmt} files in item {args.item!r}", file=sys.stderr)
            return EXIT_ERROR

        if args.dry_run:
            total = sum(f.size for f in files)
            for f in files:
                print(f"{f.size:>12}  {f.name}")
            print(f"{len(files)} file(s), {total} bytes ({human(total)}) -> {dest}")
            return 0

        dest.mkdir(parents=True, exist_ok=True)
        plans = plan_downloads(files, dest)
        pending = [p for p in plans if not p.done]
        print(
            f"{len(plans)} file(s); {len(plans) - len(pending)} present, "
            f"{len(pending)} to fetch ({human(sum(p.remaining for p in pending))})",
            file=sys.stderr,
        )

        for i, plan in enumerate(plans):
            if plan.done:
                print(f"  {plan.file.name}  already complete", file=sys.stderr)
                continue
            if plan.resume_from:
                print(f"  resuming at {human(plan.resume_from)}", file=sys.stderr)
            try:
                download_one(client, args.item, plan)
            except KeyboardInterrupt:
                print(f"\ninterrupted; partial kept at {plan.part}", file=sys.stderr)
                return EXIT_INTERRUPT
            except ChecksumError as exc:
                print(f"\nerror: {exc}", file=sys.stderr)
                return EXIT_CHECKSUM
            except (httpx.HTTPError, OSError, RuntimeError) as exc:
                print(f"\nerror: {plan.file.name}: {exc}", file=sys.stderr)
                return EXIT_ERROR
            if args.sleep and i < len(plans) - 1:
                time.sleep(args.sleep)

    provenance = build_provenance(args.item, metadata, files, license_url)
    (dest / "PROVENANCE.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"wrote {dest / 'PROVENANCE.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(EXIT_INTERRUPT) from None
