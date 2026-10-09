"""Verify a staged public proof bundle before an operator deploys it.

Generated media stays out of Git. A successful static build or HTTP 200 is
not sufficient: each media/caption asset must match its approved receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

PROOFS = ("survive-technology", "operators")


def validate_bundle(root: Path) -> list[dict]:
    root = root.resolve()
    results = []
    for name in PROOFS:
        receipt = json.loads((root / "receipts" / f"{name}.receipt.json").read_text())
        if receipt.get("approval", {}).get("status") != "approved":
            raise ValueError(f"{name}: missing approved receipt")
        for kind in ("video", "captions"):
            expected = receipt["output"][kind]
            relative = urlparse(expected["path"]).path.lstrip("/")
            asset = (root / relative).resolve()
            if not asset.is_relative_to(root):
                raise ValueError(f"{name}: asset escapes bundle")
            data = asset.read_bytes()
            if len(data) != expected["bytes"]:
                raise ValueError(f"{relative}: byte count does not match approved receipt")
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected["sha256"]:
                raise ValueError(f"{relative}: SHA-256 does not match approved receipt")
            if kind == "video":
                if data[4:8] != b"ftyp":
                    raise ValueError(f"{relative}: expected MP4, not an HTML fallback")
                probe = json.loads(
                    subprocess.check_output(
                        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(asset)]
                    )
                )
                videos = [s for s in probe["streams"] if s["codec_type"] == "video"]
                if not videos:
                    raise ValueError(f"{relative}: no decodable video stream")
            elif not data.lstrip().startswith(b"WEBVTT"):
                raise ValueError(f"{relative}: expected WebVTT")
            results.append({"path": relative, "bytes": len(data), "sha256": digest})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        results = validate_bundle(args.bundle)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Public proof bundle is incomplete or invalid: {error}\n")
    print(json.dumps({"status": "passed", "assets": results}, indent=2))


if __name__ == "__main__":
    main()
