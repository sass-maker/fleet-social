from __future__ import annotations

import copy

from mashup.extension.assets import Assets
from mashup.extension.models import Approval, Composition, canonical_hash, now
from mashup.models import EDL, Clip, Role, VisualInsert

TEMPLATE_SCHEMA = "fleet.mashup-template.v1"


def propose(composition: Composition, assets: Assets, plan_id: str) -> dict:
    background = assets.get(composition.background, verify=True)
    foreground = assets.get(composition.foreground, verify=True)
    pixel_format = foreground["media"]["pixelFormat"]
    if not (
        pixel_format.startswith(("yuva", "gbrap", "rgba", "bgra", "ya"))
        or pixel_format in {"argb", "abgr", "pal8"}
    ):
        raise ValueError(
            "Foreground needs decoded alpha (PNG/GIF or alpha MOV). "
            "Tagged WebM alpha is not supported."
        )
    duration = composition.duration
    if (
        not composition.loop
        and foreground["kind"] == "video"
        and foreground["media"]["duration"] < duration
    ):
        raise ValueError(
            "Foreground is shorter than the requested duration; enable loop or shorten it"
        )
    clip = Clip(
        index=0,
        segment_id=background["id"],
        source_id=background["id"],
        source_title=background["rights"]["title"],
        source_path=str(assets.path(background)),
        start=0,
        end=duration,
        render_start=0,
        render_end=duration,
        text="",
        summary="Approved template composition",
        role=Role.SETUP,
        energy=0.5,
        visuals=[
            VisualInsert(
                mode="motion" if foreground["kind"] == "video" else "still",
                start=0,
                end=duration,
                source_path=str(assets.path(foreground)),
                source_title=foreground["rights"]["title"],
                source_url=foreground["rights"]["sourceUrl"],
            )
        ],
    )
    timestamp = now()
    edl = EDL(
        strategy="character-overlay@1",
        prompt="Operator-configured local character overlay",
        target_duration=duration,
        generated_at=timestamp,
        clips=[clip],
        rationale=[
            "Template placement is operator-configured; editorial scores are not model judgments."
        ],
    )
    source_records = []
    for asset in {background["id"]: background, foreground["id"]: foreground}.values():
        rights = asset["rights"]
        source_records.append(
            {
                "id": asset["id"],
                "path": str(assets.path(asset)),
                "sha256": asset["sha256"],
                **{
                    key: rights[key]
                    for key in ("title", "creator", "sourceUrl", "license", "licenseUrl")
                },
            }
        )
    edit = {
        "schema": "fleet.podcast-edit.v1",
        "id": plan_id,
        "revision": 1,
        "createdAt": timestamp,
        "approval": {"status": "proposed", "approvedAt": None, "approvedBy": None},
        "sources": source_records,
        "presentation": {"sourceHeading": False, "watermark": False, "subtitles": "none"},
        "visualCues": [],
        "editorial": edl.model_dump(mode="json"),
        "template": {
            "schema": TEMPLATE_SCHEMA,
            "id": composition.template,
            "composition": composition.model_dump(),
            "assets": {asset["id"]: asset["sha256"] for asset in (background, foreground)},
        },
    }
    edit["template"]["planHash"] = canonical_hash(edit)
    return edit


def validate(edit: dict, assets: Assets) -> Composition:
    candidate = copy.deepcopy(edit)
    metadata = candidate.get("template", {})
    if metadata.get("schema") != TEMPLATE_SCHEMA:
        raise ValueError("Expected a versioned Mashup template proposal")
    expected_hash = metadata.pop("planHash", None)
    candidate["approval"] = {"status": "proposed", "approvedAt": None, "approvedBy": None}
    if canonical_hash(candidate) != expected_hash:
        raise ValueError("Plan changed after proposal; create and approve a new plan")
    composition = Composition.model_validate(metadata["composition"])
    for asset_id in (composition.background, composition.foreground):
        asset = assets.get(asset_id, verify=True)
        if metadata["assets"].get(asset_id) != asset["sha256"]:
            raise ValueError("Plan asset no longer matches its approved identity")
    EDL.model_validate(edit["editorial"])
    return composition


def approve(edit: dict, approval: Approval, assets: Assets) -> dict:
    validate(edit, assets)
    if approval.planHash != edit["template"]["planHash"] or not approval.approvedBy.strip():
        raise ValueError("Explicit approval must name the exact current plan hash and approver")
    approved = copy.deepcopy(edit)
    approved["approval"] = {
        "status": "approved",
        "approvedBy": approval.approvedBy.strip(),
        "approvedAt": now(),
        "attestation": "mcp-client",
    }
    return approved
