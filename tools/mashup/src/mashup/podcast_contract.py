"""Versioned approved-edit contract owned by Mashup.

The planner's EDL remains the internal document edited by people and consumed
by the Python renderer. ``fleet.podcast-edit.v1`` wraps that EDL with the
approval and rights evidence Mashup requires before it can render.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from mashup.content_integrity import validate_unique_content
from mashup.models import EDL

PODCAST_EDIT_SCHEMA = "fleet.podcast-edit.v1"
ApprovalStatus = Literal["proposed", "approved", "rejected"]


def _read_provenance(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read provenance file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("provenance must be a JSON object")
    return payload


def _required_text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"provenance.{field} is required")
    return value.strip()


def _source_records(edl: EDL, provenance: dict) -> list[dict]:
    files = provenance.get("files")
    if not isinstance(files, list):
        raise ValueError("provenance.files must be an array")
    by_name = {
        item.get("filename"): item
        for item in files
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    creator = _required_text(provenance, "creator")
    license_name = _required_text(provenance, "license")
    license_url = _required_text(provenance, "license_url")
    feed = _required_text(provenance, "feed")

    records: list[dict] = []
    seen: set[str] = set()
    for clip in edl.clips:
        if clip.source_id in seen:
            continue
        item = by_name.get(Path(clip.source_path).name)
        if item is None:
            raise ValueError(f"provenance has no file entry for {Path(clip.source_path).name}")
        source_url = item.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError(f"provenance source URL missing for {clip.source_id}")
        record = {
            "id": clip.source_id,
            "title": clip.source_title or clip.source_id,
            "path": clip.source_path,
            "creator": creator,
            "sourceUrl": source_url.strip(),
            "collectionUrl": feed,
            "license": license_name,
            "licenseUrl": license_url,
        }
        sha256 = item.get("sha256")
        if isinstance(sha256, str) and sha256.strip():
            record["sha256"] = sha256.strip()
        records.append(record)
        seen.add(clip.source_id)
    return records


def export_podcast_edit(
    edl: EDL,
    *,
    edit_id: str,
    provenance_path: Path,
    approval_status: ApprovalStatus = "proposed",
    approved_by: str | None = None,
    watermark_text: str = "MASHUP",
) -> dict:
    """Wrap an EDL in Mashup's canonical approved editorial contract."""
    edit_id = edit_id.strip()
    if not edit_id:
        raise ValueError("edit_id is required")
    if approval_status not in {"proposed", "approved", "rejected"}:
        raise ValueError(f"unsupported approval status: {approval_status}")
    if approval_status == "approved" and not (approved_by and approved_by.strip()):
        raise ValueError("approved edits require approved_by")
    watermark_text = watermark_text.strip()
    if not watermark_text:
        raise ValueError("watermark_text is required")
    validate_unique_content(edl.clips)

    provenance = _read_provenance(provenance_path)
    approval = {
        "status": approval_status,
        "approvedAt": edl.generated_at if approval_status == "approved" else None,
        "approvedBy": approved_by.strip() if approved_by and approved_by.strip() else None,
    }
    return {
        "schema": PODCAST_EDIT_SCHEMA,
        "id": edit_id,
        "revision": 1,
        "createdAt": edl.generated_at,
        "approval": approval,
        "presentation": {
            "sourceHeading": True,
            "watermark": True,
            "watermarkText": watermark_text,
            "subtitles": "sidecar",
        },
        "sources": _source_records(edl, provenance),
        "visualCues": [],
        "editorial": edl.model_dump(mode="json"),
    }


def save_podcast_edit(payload: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
