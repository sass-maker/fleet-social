#!/usr/bin/env python3
"""Build a source-independent local MCP bundle. Nothing is published."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
BUNDLE_FILES = (
    "install.py",
    "smoke.py",
    "README.md",
    "mashup-media/SKILL.md",
    "mashup-media/character-overlay.json",
    "pace/mashup-media.skill.md",
)


def run(*arguments: str) -> str:
    return subprocess.run(
        arguments, cwd=PROJECT, capture_output=True, text=True, check=True, timeout=120
    ).stdout.strip()


def build(output: Path, *, allow_dirty: bool) -> dict:
    output = output.expanduser().resolve()
    if output == PROJECT or PROJECT in output.parents:
        raise ValueError("Build output must be outside the source project")
    revision = run("git", "rev-parse", "HEAD")
    dirty = bool(run("git", "status", "--porcelain", "--", "."))
    if dirty and not allow_dirty:
        raise ValueError("Commit the package before building, or use --allow-dirty for development")
    version = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]["version"]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mashup-build-", dir=output) as temporary:
        bundle = Path(temporary) / "bundle"
        bundle.mkdir()
        for name in BUNDLE_FILES:
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / "extension_bundle" / name, target)
        extension = json.loads((PROJECT / "extension.json").read_text())
        extension["installation"]["guide"] = "README.md"
        (bundle / "extension.json").write_text(json.dumps(extension, indent=2) + "\n")
        wheel_directory = Path(temporary) / "wheel"
        run("uv", "build", "--wheel", "--out-dir", str(wheel_directory))
        run(
            "uv",
            "export",
            "--locked",
            "--no-default-groups",
            "--extra",
            "mcp",
            "--no-emit-project",
            "--no-header",
            "--no-annotate",
            "--output-file",
            str(bundle / "requirements.lock"),
        )
        wheel = "mashup-" + version + "-py3-none-any.whl"
        shutil.copyfile(wheel_directory / wheel, bundle / wheel)
        files = {
            str(path.relative_to(bundle)): {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(bundle.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema": "fleet.mashup-extension-bundle.v1",
            "id": "mashup-media",
            "version": version,
            "sourceRevision": revision,
            "sourceDirty": dirty,
            "wheel": wheel,
            "files": files,
        }
        identity = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        (bundle / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")
        name = "mashup-media-" + version + "-" + identity[:20]
        destination = output / name
        if destination.exists():
            if (destination / "bundle.json").read_bytes() != (bundle / "bundle.json").read_bytes():
                raise ValueError("Existing bundle conflicts; choose another output directory")
            for path, expected in files.items():
                if (
                    hashlib.sha256((destination / path).read_bytes()).hexdigest()
                    != expected["sha256"]
                ):
                    raise ValueError("Existing bundle changed; choose another output directory")
        else:
            shutil.copytree(bundle, destination)
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    info = tarfile.TarInfo(name + "/" + str(path.relative_to(bundle)))
                    payload = path.read_bytes()
                    info.size = len(payload)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(payload))
        archive_path = output / (name + ".tar.gz")
        archive_bytes = gzip.compress(data.getvalue(), mtime=0)
        if archive_path.exists() and archive_path.read_bytes() != archive_bytes:
            raise ValueError("Existing archive changed; choose another output directory")
        if not archive_path.exists():
            archive_path.write_bytes(archive_bytes)
        return {
            "schema": "fleet.mashup-extension-build.v1",
            "bundle": str(destination),
            "archive": str(archive_path),
            "bundleId": identity,
            "sourceRevision": revision,
            "sourceDirty": dirty,
            "archiveSha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.output, allow_dirty=args.allow_dirty), indent=2))


if __name__ == "__main__":
    main()
