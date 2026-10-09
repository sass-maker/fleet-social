"""Regression for the missing-MP4 / successful-HTML fallback release failure."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts/check_public_proof.py"
spec = importlib.util.spec_from_file_location("check_public_proof", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_missing_media_fails_even_with_an_approved_receipt(tmp_path):
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "survive-technology.receipt.json").write_text(
        json.dumps(
            {
                "approval": {"status": "approved"},
                "output": {
                    "video": {"path": "/media/missing.mp4", "bytes": 10, "sha256": "0" * 64}
                },
            }
        )
    )
    with pytest.raises(FileNotFoundError):
        module.validate_bundle(tmp_path)


def test_html_is_not_video_even_when_bytes_and_hash_match(tmp_path):
    html = b"<!DOCTYPE html><title>Mashup</title>"
    (tmp_path / "media").mkdir()
    (tmp_path / "media/proof.mp4").write_bytes(html)
    (tmp_path / "receipts").mkdir()
    (tmp_path / "receipts/survive-technology.receipt.json").write_text(
        json.dumps(
            {
                "approval": {"status": "approved"},
                "output": {
                    "video": {
                        "path": "/media/proof.mp4",
                        "bytes": len(html),
                        "sha256": hashlib.sha256(html).hexdigest(),
                    }
                },
            }
        )
    )
    with pytest.raises(ValueError, match="HTML fallback"):
        module.validate_bundle(tmp_path)
