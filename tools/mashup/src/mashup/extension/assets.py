from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from mashup.extension.models import Rights, canonical_hash
from mashup.extension.paths import open_beneath

MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_ASSETS = 128
STILL_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
MEDIA_SUFFIXES = STILL_SUFFIXES | {".mov", ".mp4", ".webm", ".gif"}
MEDIA_FORMATS = "mov,matroska,webm,gif,png_pipe,jpeg_pipe,bmp_pipe,image2"
ASSET_ID = re.compile(r"asset-[a-f0-9]{64}\Z")


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            shutil.which("ffprobe") or "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            MEDIA_FORMATS,
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if result.returncode or len(result.stdout) > 256_000:
        raise ValueError("Local media could not be probed")
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or not 0 < video.get("width", 0) <= 8192 or not 0 < video.get("height", 0) <= 8192:
        raise ValueError("Asset must contain video or an image, at most 8192 pixels per side")
    return {
        "width": video["width"],
        "height": video["height"],
        "duration": float(data.get("format", {}).get("duration") or video.get("duration") or 0),
        "pixelFormat": video.get("pix_fmt", "unknown"),
        "hasAudio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


class Assets:
    def __init__(self, state: Path, input_roots: list[Path]):
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = state / "assets"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.roots = [root.expanduser().resolve(strict=True) for root in input_roots]
        if any(not root.is_dir() for root in self.roots):
            raise ValueError("Each input root must be an existing directory")

    def register(self, source: str, rights: Rights) -> dict:
        requested = Path(source).expanduser()
        if not requested.is_absolute() or ".." in requested.parts:
            raise ValueError("Provide an absolute local path without traversal")
        path = requested.resolve(strict=True)
        if not any(path.is_relative_to(root) for root in self.roots):
            raise ValueError("Asset is outside the explicitly allowed input roots")
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            raise ValueError("Unsupported local asset type")
        if not 0 < path.stat().st_size <= MAX_ASSET_BYTES:
            raise ValueError("Asset must be nonempty and at most 128 MiB")
        fd, temporary = tempfile.mkstemp(
            prefix="import-", suffix=path.suffix.lower(), dir=self.directory
        )
        snapshot = Path(temporary)
        try:
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(fd, "wb") as target, open_beneath(path, self.roots) as origin:
                for chunk in iter(lambda: origin.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > MAX_ASSET_BYTES:
                        raise ValueError("Asset exceeded the import size limit")
                    digest.update(chunk)
                    target.write(chunk)
            info = probe(snapshot)
            identity = {
                "sha256": digest.hexdigest(),
                "rights": rights.model_dump(),
                "suffix": path.suffix.lower(),
            }
            asset_id = "asset-" + canonical_hash(identity)
            metadata_path = self.directory / f"{asset_id}.json"
            if metadata_path.exists():
                return self.public(self.get(asset_id, verify=True))
            if len(list(self.directory.glob("asset-*.json"))) >= MAX_ASSETS:
                raise ValueError("Asset registry limit reached (128); choose a new state directory")
            destination = self.directory / f"{asset_id}{path.suffix.lower()}"
            metadata = {
                "id": asset_id,
                **identity,
                "bytes": size,
                "media": info,
                "kind": "image" if path.suffix.lower() in STILL_SUFFIXES else "video",
                "filename": destination.name,
            }
            os.replace(snapshot, destination)
            temporary_metadata = snapshot.with_suffix(".json")
            temporary_metadata.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
            os.replace(temporary_metadata, metadata_path)
            return self.public(metadata)
        finally:
            snapshot.unlink(missing_ok=True)

    def get(self, asset_id: str, *, verify: bool = False) -> dict:
        if not ASSET_ID.fullmatch(asset_id):
            raise ValueError("Invalid asset ID")
        metadata = json.loads((self.directory / f"{asset_id}.json").read_text(encoding="utf-8"))
        path = self.directory / metadata["filename"]
        if path.parent != self.directory or not path.is_file():
            raise ValueError("Stored asset is unavailable")
        if verify:
            digest = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != metadata["sha256"]:
                raise ValueError("Stored asset no longer matches its registered hash")
        return metadata

    def path(self, metadata: dict) -> Path:
        return self.directory / metadata["filename"]

    @staticmethod
    def public(metadata: dict) -> dict:
        return {key: value for key, value in metadata.items() if key != "filename"}
