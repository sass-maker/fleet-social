"""Strict, non-interactive agent operations for Mashup."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mashup import pipeline
from mashup.collections import get_collection, list_collections
from mashup.config import load_config
from mashup.media_receipt import build_media_receipt, validate_media_receipt
from mashup.models import EDL
from mashup.plan.prompt import parse_duration
from mashup.podcast_contract import export_podcast_edit
from mashup.render import load_edl, render, save_edl
from mashup.shorts import validate_short_duration
from mashup.store import Store

AGENT_SCHEMA = "fleet.video-agent-operation.v1"
MANIFEST_SCHEMA = "fleet.video-agent-manifest.v1"
PRODUCT = "mashup"
ROOT_FIELDS = {
    "schema",
    "product",
    "operation",
    "operationId",
    "idempotencyKey",
    "validateOnly",
    "input",
}
FORBIDDEN_FIELDS = {"command", "shell", "script", "sourceCode", "code", "plugin", "executable"}

OPERATIONS = [
    ("manifest", "read"),
    ("collections", "read"),
    ("models", "read"),
    ("status", "read"),
    ("ingest", "write"),
    ("enrich", "write"),
    ("embed", "write"),
    ("plan", "plan"),
    ("short-plan", "plan"),
    ("short-batch-plan", "plan"),
    ("validate-edl", "read"),
    ("export-podcast-edit", "write"),
    ("validate-render", "plan"),
    ("render", "render"),
    ("media-receipt", "write"),
    ("inspect-receipt", "read"),
]


class AgentError(ValueError):
    def __init__(
        self, code: str, message: str, *, path: str | None = None, retryable: bool = False
    ):
        super().__init__(message)
        self.code = code
        self.path = path
        self.retryable = retryable


def run_agent(
    raw: Any, *, progress: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    request = _normalize_request(raw)
    started = _now()
    operation = request["operation"]
    handlers = {
        "manifest": _manifest,
        "collections": _collections,
        "models": _models,
        "status": _status,
        "ingest": _ingest,
        "enrich": _enrich,
        "embed": _embed,
        "plan": _plan,
        "short-plan": _short_plan,
        "short-batch-plan": _short_batch_plan,
        "validate-edl": _validate_edl,
        "export-podcast-edit": _export_edit,
        "validate-render": _validate_render,
        "render": _render,
        "media-receipt": _media_receipt,
        "inspect-receipt": _inspect_receipt,
    }
    handler = handlers.get(operation)
    if handler is None:
        raise AgentError(
            "UNKNOWN_OPERATION", f"unknown Mashup operation: {operation}", path="operation"
        )
    side_effect = dict(OPERATIONS)[operation]
    result, artifacts, warnings = handler(request, progress)
    finished = _now()
    return {
        "schema": AGENT_SCHEMA,
        "product": PRODUCT,
        "operation": operation,
        "operationId": request["operationId"],
        "idempotencyKey": request.get("idempotencyKey"),
        "state": "validated" if request["validateOnly"] else "completed",
        "sideEffect": "plan" if request["validateOnly"] else side_effect,
        "startedAt": started,
        "finishedAt": finished,
        "requestHash": _stable_hash({"operation": operation, "input": request["input"]}),
        "result": result,
        "warnings": warnings,
        "artifacts": artifacts,
        "error": None,
    }


def failure(raw: Any, error: Exception) -> dict[str, Any]:
    now = _now()
    code = getattr(error, "code", "OPERATION_FAILED")
    return {
        "schema": AGENT_SCHEMA,
        "product": raw.get("product", PRODUCT) if isinstance(raw, dict) else PRODUCT,
        "operation": raw.get("operation") if isinstance(raw, dict) else None,
        "operationId": raw.get("operationId") if isinstance(raw, dict) else None,
        "idempotencyKey": raw.get("idempotencyKey") if isinstance(raw, dict) else None,
        "state": "failed",
        "sideEffect": "none",
        "startedAt": now,
        "finishedAt": now,
        "requestHash": None,
        "result": None,
        "warnings": [],
        "artifacts": [],
        "error": {
            "code": code,
            "message": str(error),
            "path": getattr(error, "path", None),
            "retryable": bool(getattr(error, "retryable", False)),
            "details": None,
        },
    }


def read_agent_request(path: Path | None) -> Any:
    text = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    if not text.strip():
        raise AgentError("INVALID_REQUEST", "provide one JSON request on stdin or with --request")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentError("INVALID_JSON", f"invalid JSON: {exc}") from exc


def _manifest(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], set(), "input")
    return (
        {
            "schema": MANIFEST_SCHEMA,
            "product": PRODUCT,
            "transport": {"kind": "cli-json", "foreground": True, "stdout": "single-json-envelope"},
            "operations": [
                {"id": name, "sideEffect": effect, "validateOnly": effect not in {"read"}}
                for name, effect in OPERATIONS
            ],
            "stages": ["ingest", "enrich", "embed", "plan", "approval", "render", "receipt"],
            "resumability": {
                "store": "sqlite",
                "reusedStagesReported": True,
                "processSurvival": False,
            },
            "safety": {
                "arbitraryExecution": False,
                "renderRequiresApproval": True,
                "localByDefault": True,
            },
        },
        [],
        [],
    )


def _collections(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], set(), "input")
    return (
        {"collections": [preset.model_dump(mode="json") for preset in list_collections()]},
        [],
        [],
    )


def _models(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], {"workdir"}, "input")
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    return (
        {
            "chat": {
                "backend": cfg.chat_backend,
                "model": cfg.local_chat_model if cfg.chat_backend == "local" else cfg.chat_model,
            },
            "embed": {
                "backend": cfg.embed_backend,
                "model": cfg.local_embed_model if cfg.embed_backend == "local" else cfg.embed_model,
            },
            "gatewayRequired": cfg.needs_gateway,
        },
        [],
        [],
    )


def _status(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], {"workdir"}, "input")
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    if not cfg.db_path.exists():
        return (
            {"ready": False, "database": str(cfg.db_path), "counts": {}, "embeddingModels": {}},
            [],
            [],
        )
    with Store(cfg.db_path) as store:
        return (
            {
                "ready": True,
                "database": str(cfg.db_path),
                "counts": store.counts(),
                "embeddingModels": store.embedding_models(),
            },
            [],
            [],
        )


def _ingest(request: dict, progress) -> tuple[dict, list, list]:
    _exact(request["input"], {"workdir", "inputDir", "transcribe"}, "input")
    input_dir = _required_path(request["input"], "inputDir")
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    if request["validateOnly"]:
        return (
            {"ready": input_dir.is_dir(), "inputDir": str(input_dir), "database": str(cfg.db_path)},
            [],
            [],
        )
    events: list[dict] = []
    counts = pipeline.ingest(
        input_dir,
        cfg,
        allow_transcribe=request["input"].get("transcribe", True),
        progress=lambda message: _emit(progress, events, "ingest", message),
    )
    return ({"counts": counts, "events": events}, [], [])


def _enrich(request: dict, progress) -> tuple[dict, list, list]:
    return _pipeline_stage(request, progress, "enrich")


def _embed(request: dict, progress) -> tuple[dict, list, list]:
    return _pipeline_stage(request, progress, "embed")


def _pipeline_stage(request: dict, progress, stage: str) -> tuple[dict, list, list]:
    allowed = {"workdir", "concurrency"} if stage == "enrich" else {"workdir", "reset"}
    _exact(request["input"], allowed, "input")
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    if request["validateOnly"]:
        return (
            {"ready": cfg.db_path.exists(), "stage": stage, "database": str(cfg.db_path)},
            [],
            [],
        )
    events: list[dict] = []

    def callback(done: int, total: int) -> None:
        _emit(progress, events, stage, {"done": done, "total": total})

    if stage == "enrich":
        counts = pipeline.enrich(
            cfg, concurrency=int(request["input"].get("concurrency", 4)), progress=callback
        )
    else:
        counts = pipeline.embed(
            cfg,
            reset=bool(request["input"].get("reset", False)),
            progress=callback,
            notice=lambda message: _emit(progress, events, stage, message),
        )
    return ({"counts": counts, "events": events}, [], [])


def _plan(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(
        request["input"],
        {"workdir", "prompt", "duration", "variants", "output", "snap", "crossfade", "baselines"},
        "input",
    )
    prompt = _required_text(request["input"], "prompt")
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    target = parse_duration(prompt, float(request["input"].get("duration", 420)))
    variants = int(request["input"].get("variants", 3))
    if variants < 1 or variants > 3:
        raise AgentError("INVALID_INPUT", "variants must be between 1 and 3", path="input.variants")
    if request["validateOnly"]:
        return (
            {"ready": cfg.db_path.exists(), "targetSeconds": target, "variants": variants},
            [],
            [],
        )
    edls = pipeline.make_mashups(
        prompt,
        cfg,
        target=target,
        strategies=pipeline.AI_STRATEGIES[:variants],
        include_baselines=bool(request["input"].get("baselines", False)),
        snap=bool(request["input"].get("snap", True)),
        crossfade=float(request["input"].get("crossfade", 0)),
    )
    return _save_edls(edls, request["input"].get("output"))


def _short_plan(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], {"workdir", "prompt", "duration", "output"}, "input")
    prompt = _required_text(request["input"], "prompt")
    target = validate_short_duration(float(request["input"].get("duration", 45)))
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    if request["validateOnly"]:
        return ({"ready": cfg.db_path.exists(), "targetSeconds": target}, [], [])
    return _save_edls(
        [pipeline.make_short(prompt, cfg, target=target)], request["input"].get("output")
    )


def _short_batch_plan(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(
        request["input"],
        {"workdir", "collection", "angle", "prompt", "duration", "count", "output"},
        "input",
    )
    try:
        preset = get_collection(str(request["input"].get("collection", "startups")))
        angle, prompt = preset.prompt_for(
            request["input"].get("angle"), request["input"].get("prompt")
        )
        target = validate_short_duration(float(request["input"].get("duration", 45)))
        count = int(request["input"].get("count", 5))
        if not 3 <= count <= 5:
            raise ValueError("count must be between 3 and 5")
    except ValueError as exc:
        raise AgentError("INVALID_INPUT", str(exc), path="input") from exc
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    if request["validateOnly"]:
        counts: dict[str, int] = {}
        if cfg.db_path.exists():
            with Store(cfg.db_path) as store:
                counts = store.counts()
        return (
            {
                "ready": counts.get("embedded", 0) >= count,
                "collection": preset.id,
                "angle": angle,
                "prompt": prompt,
                "targetSeconds": target,
                "count": count,
                "counts": counts,
            },
            [],
            [],
        )
    result, artifacts, warnings = _save_edls(
        pipeline.make_short_batch(prompt, cfg, count=count, target=target),
        request["input"].get("output"),
    )
    result.update({"collection": preset.id, "angle": angle, "prompt": prompt})
    return result, artifacts, warnings


def _save_edls(edls: list[EDL], output: Any) -> tuple[dict, list, list]:
    artifacts = []
    if output:
        directory = Path(str(output)).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        for edl in edls:
            destination = directory / f"{edl.strategy}.json"
            save_edl(edl, destination)
            artifacts.append(
                {"kind": "edl", "path": str(destination), "sha256": _file_hash(destination)}
            )
    return (
        {"variants": [edl.model_dump(mode="json") for edl in edls], "count": len(edls)},
        artifacts,
        [],
    )


def _validate_edl(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], {"edlPath"}, "input")
    path = _required_path(request["input"], "edlPath")
    edl = load_edl(path)
    return (
        {
            "valid": True,
            "strategy": edl.strategy,
            "clips": len(edl.clips),
            "durationSeconds": edl.duration,
            "score": edl.score,
        },
        [],
        [],
    )


def _export_edit(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(
        request["input"],
        {
            "edlPath",
            "provenancePath",
            "editId",
            "approval",
            "approvedBy",
            "watermarkText",
            "output",
        },
        "input",
    )
    edl = load_edl(_required_path(request["input"], "edlPath"))
    payload = export_podcast_edit(
        edl,
        edit_id=_required_text(request["input"], "editId"),
        provenance_path=_required_path(request["input"], "provenancePath"),
        approval_status=request["input"].get("approval", "proposed"),
        approved_by=request["input"].get("approvedBy"),
        watermark_text=request["input"].get("watermarkText", "MASHUP"),
    )
    if request["validateOnly"] or not request["input"].get("output"):
        return ({"edit": payload}, [], [])
    destination = Path(str(request["input"]["output"])).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return (
        {"edit": payload},
        [{"kind": "podcast-edit", "path": str(destination), "sha256": _file_hash(destination)}],
        [],
    )


def _validate_render(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], {"podcastEditPath", "output"}, "input")
    payload = _approved_edit(_required_path(request["input"], "podcastEditPath"))
    return (
        {
            "ready": True,
            "editId": payload["id"],
            "output": str(
                Path(str(request["input"].get("output", "output.mp4"))).expanduser().resolve()
            ),
        },
        [],
        [],
    )


def _render(request: dict, progress) -> tuple[dict, list, list]:
    _exact(
        request["input"],
        {
            "podcastEditPath",
            "output",
            "workdir",
            "crossfade",
            "subtitles",
            "sourceLabel",
            "watermark",
            "watermarkText",
            "profile",
        },
        "input",
    )
    payload = _approved_edit(_required_path(request["input"], "podcastEditPath"))
    output = _required_path(request["input"], "output", must_exist=False)
    if request["validateOnly"]:
        return ({"ready": True, "editId": payload["id"], "output": str(output)}, [], [])
    cfg = load_config(_path(request["input"].get("workdir")), require_key=False)
    profile = str(request["input"].get("profile", "source"))
    if profile not in {"source", "social"}:
        raise AgentError("INVALID_INPUT", "profile must be source or social", path="input.profile")
    edl = EDL.model_validate(payload["editorial"])
    events: list[dict] = []
    render(
        edl,
        output,
        crossfade=float(request["input"].get("crossfade", 0)),
        subtitles=request["input"].get("subtitles", "sidecar"),
        source_label=bool(request["input"].get("sourceLabel", True)),
        watermark=bool(request["input"].get("watermark", True)),
        watermark_text=request["input"].get("watermarkText", "MASHUP"),
        profile=profile,
        workdir=cfg.workdir,
        progress=lambda message: _emit(progress, events, "render", message),
    )
    return (
        {"editId": payload["id"], "events": events},
        [{"kind": "video", "path": str(output), "sha256": _file_hash(output)}],
        [],
    )


def _media_receipt(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(
        request["input"],
        {
            "podcastEditPath",
            "videoPath",
            "captionsPath",
            "durationSeconds",
            "width",
            "height",
            "output",
        },
        "input",
    )
    payload = json.loads(
        _required_path(request["input"], "podcastEditPath").read_text(encoding="utf-8")
    )
    receipt = build_media_receipt(
        payload,
        video_path=_required_path(request["input"], "videoPath"),
        captions_path=_path(request["input"].get("captionsPath")),
        duration_seconds=float(request["input"]["durationSeconds"]),
        width=int(request["input"]["width"]),
        height=int(request["input"]["height"]),
        operation={
            "id": request["operationId"],
            "schema": AGENT_SCHEMA,
            "requestHash": _stable_hash(
                {"operation": request["operation"], "input": request["input"]}
            ),
            "state": "completed",
        },
    )
    artifacts = []
    if request["input"].get("output") and not request["validateOnly"]:
        destination = Path(str(request["input"]["output"])).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        artifacts.append(
            {"kind": "media-receipt", "path": str(destination), "sha256": _file_hash(destination)}
        )
    return ({"receipt": receipt}, artifacts, [])


def _inspect_receipt(request: dict, _progress) -> tuple[dict, list, list]:
    _exact(request["input"], {"receiptPath"}, "input")
    payload = json.loads(
        _required_path(request["input"], "receiptPath").read_text(encoding="utf-8")
    )
    validate_media_receipt(payload)
    return ({"valid": True, "receipt": payload}, [], [])


def _approved_edit(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "fleet.podcast-edit.v1":
        raise AgentError("INVALID_EDIT", "expected fleet.podcast-edit.v1")
    if payload.get("approval", {}).get("status") != "approved":
        raise AgentError("APPROVAL_REQUIRED", "Mashup rendering requires an approved edit")
    EDL.model_validate(payload.get("editorial"))
    return payload


def _normalize_request(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise AgentError("INVALID_REQUEST", "request must be a JSON object")
    _exact(raw, ROOT_FIELDS, "request")
    if raw.get("schema") != AGENT_SCHEMA:
        raise AgentError("UNSUPPORTED_SCHEMA", f"schema must be {AGENT_SCHEMA}", path="schema")
    if raw.get("product") != PRODUCT:
        raise AgentError("PRODUCT_MISMATCH", f"product must be {PRODUCT}", path="product")
    operation = _required_text(raw, "operation")
    inputs = raw.get("input", {})
    if not isinstance(inputs, dict):
        raise AgentError("INVALID_INPUT", "input must be an object", path="input")
    _reject_forbidden(inputs, "input")
    return {
        **raw,
        "operation": operation,
        "operationId": raw.get("operationId") or str(uuid.uuid4()),
        "validateOnly": raw.get("validateOnly") is True,
        "input": inputs,
    }


def _exact(value: dict, allowed: set[str], path: str) -> None:
    for key in value:
        if key not in allowed:
            raise AgentError("UNKNOWN_FIELD", f"unknown field: {path}.{key}", path=f"{path}.{key}")


def _reject_forbidden(value: Any, path: str) -> None:
    if isinstance(value, list):
        for index, entry in enumerate(value):
            _reject_forbidden(entry, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, entry in value.items():
            if key in FORBIDDEN_FIELDS:
                raise AgentError(
                    "ARBITRARY_EXECUTION_REJECTED",
                    f"{path}.{key} is not accepted",
                    path=f"{path}.{key}",
                )
            _reject_forbidden(entry, f"{path}.{key}")


def _required_text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AgentError("REQUIRED_FIELD", f"input.{field} is required", path=f"input.{field}")
    return value.strip()


def _required_path(payload: dict, field: str, *, must_exist: bool = True) -> Path:
    path = Path(_required_text(payload, field)).expanduser().resolve()
    if must_exist and not path.exists():
        raise AgentError(
            "PATH_NOT_FOUND", f"input.{field} does not exist: {path}", path=f"input.{field}"
        )
    return path


def _path(value: Any) -> Path | None:
    return Path(str(value)).expanduser().resolve() if value else None


def _emit(callback, events: list, stage: str, progress: Any) -> None:
    event = {
        "schema": "fleet.video-agent-event.v1",
        "stage": stage,
        "progress": progress,
        "recordedAt": _now(),
    }
    events.append(event)
    if callback:
        callback(event)


def _stable_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
