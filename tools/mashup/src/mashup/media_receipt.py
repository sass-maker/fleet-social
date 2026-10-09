"""Versioned handoff for completed Mashup media.

The receipt is deliberately independent from Mashup's SQLite state. A consumer
can verify a finished artifact using this document and the files it names.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MEDIA_RECEIPT_SCHEMA = "fleet.mashup-media-receipt.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"media artifact is missing or empty: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def build_media_receipt(
    podcast_edit: dict[str, Any],
    *,
    video_path: Path,
    duration_seconds: float,
    width: int,
    height: int,
    captions_path: Path | None = None,
    recipe_id: str = "mashup-editorial@1",
    runtime_revision: str = "mashup@0.1.0",
    model_revisions: dict[str, str] | None = None,
    generated_at: str | None = None,
    operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if podcast_edit.get("schema") != "fleet.podcast-edit.v1":
        raise ValueError("expected fleet.podcast-edit.v1 input")
    approval = podcast_edit.get("approval")
    if not isinstance(approval, dict) or approval.get("status") != "approved":
        raise ValueError("Mashup media receipts require an approved edit")
    sources = podcast_edit.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("approved edit must include source provenance")
    if duration_seconds <= 0 or width <= 0 or height <= 0:
        raise ValueError("duration and dimensions must be positive")
    for source in sources:
        required = ("id", "title", "sourceUrl", "license", "licenseUrl")
        if not isinstance(source, dict) or any(not source.get(field) for field in required):
            raise ValueError("every source must include identity, provenance, and rights")

    video = _artifact(video_path)
    captions = _artifact(captions_path) if captions_path else None
    timestamp = generated_at or datetime.now(UTC).isoformat()
    receipt = {
        "schema": MEDIA_RECEIPT_SCHEMA,
        "artifactId": podcast_edit.get("id"),
        "generatedAt": timestamp,
        "approval": approval,
        "recipe": {"id": recipe_id},
        "runtime": {"revision": runtime_revision},
        "modelRevisions": dict(sorted((model_revisions or {}).items())),
        "sources": sources,
        "output": {
            "video": video,
            "captions": captions,
            "durationSeconds": duration_seconds,
            "width": width,
            "height": height,
        },
        "validation": {
            "artifactHashVerified": True,
            "approvalVerified": True,
            "provenanceVerified": True,
        },
        "operation": operation,
    }
    return receipt


def validate_media_receipt(receipt: dict[str, Any]) -> None:
    if receipt.get("schema") != MEDIA_RECEIPT_SCHEMA:
        raise ValueError("unsupported Mashup media receipt schema")
    output = receipt.get("output")
    video = output.get("video") if isinstance(output, dict) else None
    if not isinstance(video, dict) or not all(
        video.get(field) for field in ("path", "bytes", "sha256")
    ):
        raise ValueError("Mashup media receipt requires a complete video artifact")
    operation = receipt.get("operation")
    if operation is not None:
        if (
            not isinstance(operation, dict)
            or operation.get("schema") != "fleet.video-agent-operation.v1"
        ):
            raise ValueError("invalid Mashup agent operation linkage")
        if (
            operation.get("state") != "completed"
            or not operation.get("id")
            or not operation.get("requestHash")
        ):
            raise ValueError("Mashup agent operation linkage must identify a completed request")


def save_media_receipt(receipt: dict[str, Any], path: Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
