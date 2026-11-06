from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from mashup.agent import AGENT_SCHEMA, AgentError, run_agent
from mashup.extension.assets import Assets, probe
from mashup.extension.jobs import Jobs
from mashup.extension.models import PRESETS, Approval, Composition, Rights
from mashup.extension.skills import Skills
from mashup.extension.worker import render_command, run
from mashup.media_receipt import sha256_file, validate_media_receipt


def rights(title="Original fixture") -> Rights:
    return Rights(
        title=title,
        creator="Fixture author",
        sourceUrl="urn:fixture:original",
        license="creator-owned",
        licenseUrl="urn:fixture:rights",
        rightsConfirmed=True,
    )


def ffmpeg(*args, data=None) -> bytes:
    return subprocess.run(
        [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=30,
        input=data,
    ).stdout


@pytest.fixture
def media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for local template runtime tests")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    background = inputs / "background.mp4"
    foreground = inputs / "foreground.mov"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=96x64:rate=24:duration=1",
        "-an",
        "-c:v",
        "libx264",
        str(background),
    )
    frames = bytearray()
    for index in range(6):
        color = bytes((255, 0, 0, 255) if index < 3 else (0, 255, 0, 255))
        for y in range(32):
            for x in range(32):
                frames.extend(color if 8 <= x < 24 and 8 <= y < 24 else bytes(4))
    ffmpeg(
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgba",
        "-video_size",
        "32x32",
        "-framerate",
        "24",
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "qtrle",
        str(foreground),
        data=bytes(frames),
    )
    state = tmp_path / "state"
    assets = Assets(state, [inputs])
    if os.name == "posix":
        assert state.stat().st_mode & 0o777 == 0o700
    bg = assets.register(str(background), rights("Moving background"))
    fg = assets.register(str(foreground), rights("Original alpha character"))
    return state, assets, bg, fg, inputs


def composition(bg, fg, **overrides):
    return Composition(
        background=bg["id"],
        foreground=fg["id"],
        duration=0.75,
        preset="landscape",
        x=0.5,
        y=0.5,
        width=0.25,
        **overrides,
    )


def approved_job(media, *, key="fixture-render", launch=False, motion="none"):
    state, assets, bg, fg, _ = media
    jobs = Jobs(state)
    plan = jobs.plan(composition(bg, fg, motion=motion), assets)
    approval = Approval(
        status="approved",
        approvedBy="Fixture operator explicitly approved",
        planHash=plan["planHash"],
    )
    job = jobs.start(plan["planId"], approval, key, assets, launch=launch)
    return jobs, plan, approval, job


def frame(path: Path, at: float) -> bytes:
    return ffmpeg(
        "-ss",
        str(at),
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "format=rgb24",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    )


def pixel(data: bytes, x: int, y: int, width=1280) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return tuple(data[offset : offset + 3])


def test_actual_alpha_overlay_preserves_background_placement_and_foreground_loop(media):
    jobs, _, _, job = approved_job(media)
    assert probe(media[1].path(media[1].get(media[3]["id"])))["pixelFormat"] == "argb"
    run(jobs.state)
    finished = jobs.get(job["jobId"])
    assert finished["state"] == "succeeded", finished
    result = jobs.result(job["jobId"])
    assert (result["width"], result["height"]) == PRESETS["landscape"]
    assert result["video"]["sha256"] == sha256_file(Path(result["video"]["path"]))
    receipt = json.loads(Path(result["receipt"]["path"]).read_text())
    validate_media_receipt(receipt)
    assert receipt["approval"]["status"] == "approved"
    assert receipt["recipe"]["id"] == "character-overlay@1"
    assert receipt["template"]["planHash"] == result["planHash"]
    early = frame(Path(result["video"]["path"]), 0.04)
    late = frame(Path(result["video"]["path"]), 0.54)
    middle = frame(Path(result["video"]["path"]), 0.17)
    for data in (early, late):
        r, g, b = pixel(data, 640, 360)
        assert r > 180 and g < 70 and b < 70
    r, g, b = pixel(middle, 640, 360)
    assert r < 70 and g > 180 and b < 70
    assert pixel(late, 500, 220) != (0, 0, 0)
    assert early[: 1280 * 150 * 3] != late[: 1280 * 150 * 3]


def test_motion_moves_pixels_and_unlooped_short_foreground_is_rejected(media):
    state, assets, bg, fg, _ = media
    jobs = Jobs(state)
    with pytest.raises(ValueError, match="shorter"):
        jobs.plan(composition(bg, fg, loop=False), assets)
    jobs, _, _, job = approved_job(media, motion="slide")
    run(state)
    result = jobs.result(job["jobId"])
    at_start = frame(Path(result["video"]["path"]), 0)
    at_peak = frame(Path(result["video"]["path"]), 0.5)
    assert pixel(at_start, 745, 360)[0] < 180 or pixel(at_start, 745, 360)[1] > 70
    r, g, b = pixel(at_peak, 745, 360)
    assert r > 180 and g < 70 and b < 70
    assert jobs.get(job["jobId"])["state"] == "succeeded"


def test_paths_rights_unknown_fields_and_plan_approval_are_bounded(media, tmp_path):
    state, assets, bg, fg, inputs = media
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not an allowed file")
    link = inputs / "escape.png"
    link.symlink_to(outside)
    for path in (outside, link, inputs / ".." / "outside.png"):
        with pytest.raises(ValueError, match="roots|traversal"):
            assets.register(str(path), rights())
    with pytest.raises(ValidationError):
        Rights(**{**rights().model_dump(), "rightsConfirmed": False})
    with pytest.raises(ValidationError):
        Rights(**{**rights().model_dump(), "rightsConfirmed": 1})
    with pytest.raises(ValueError, match="absolute local path"):
        assets.register("https://example.com/animation.mov", rights())
    with pytest.raises(ValidationError):
        Composition(**{**composition(bg, fg).model_dump(), "command": "echo forbidden"})
    jobs = Jobs(state)
    plan = jobs.plan(composition(bg, fg), assets)
    assert jobs.validate_plan(plan["planId"], assets)["approval"]["status"] == "proposed"
    with pytest.raises(ValueError, match="exact current plan"):
        jobs.start(
            plan["planId"],
            Approval(status="approved", approvedBy="operator", planHash="0" * 64),
            "bad",
            assets,
            launch=False,
        )
    edit_path = tmp_path / "template-edit.json"
    edit_path.write_text(json.dumps(plan["edit"]))
    for operation in ("render", "validate-render"):
        with pytest.raises(AgentError) as error:
            run_agent(
                {
                    "schema": AGENT_SCHEMA,
                    "product": "mashup",
                    "operation": operation,
                    "input": {
                        "podcastEditPath": str(edit_path),
                        "output": str(tmp_path / "wrong.mp4"),
                    },
                }
            )
        assert error.value.code == "TEMPLATE_RENDER_REQUIRED"
    plan["edit"]["template"]["composition"]["x"] = 0.1
    from mashup.extension.templates import approve

    with pytest.raises(ValueError, match="changed after proposal"):
        approve(
            plan["edit"],
            Approval(status="approved", approvedBy="operator", planHash=plan["planHash"]),
            assets,
        )


def test_durable_queue_idempotency_cancel_and_dead_worker_recovery(media):
    jobs, plan, approval, job = approved_job(media)
    assert (
        jobs.start(plan["planId"], approval, "fixture-render", media[1], launch=False)["jobId"]
        == job["jobId"]
    )
    other = jobs.plan(composition(media[2], media[3], motion="bob"), media[1])
    with pytest.raises(ValueError, match="different request"):
        jobs.start(
            other["planId"],
            Approval(status="approved", approvedBy="operator", planHash=other["planHash"]),
            "fixture-render",
            media[1],
            launch=False,
        )
    assert Jobs(jobs.state).cancel(job["jobId"])["state"] == "cancelled"
    assert jobs.cancel(job["jobId"])["state"] == "cancelled"
    with pytest.raises(ValueError, match="no published result"):
        jobs.result(job["jobId"])
    running = jobs.start(plan["planId"], approval, "recover", media[1], launch=False)
    claimed = jobs.claim()
    assert claimed["id"] == running["jobId"]
    with jobs.connection() as db:
        db.execute("UPDATE jobs SET worker_pid=? WHERE id=?", (2_000_000_000, running["jobId"]))
    assert Jobs(jobs.state).get(running["jobId"])["state"] == "failed"
    for index in range(5):
        jobs.start(plan["planId"], approval, f"pending-{index}", media[1], launch=False)
    with pytest.raises(ValueError, match="queue is full"):
        jobs.start(plan["planId"], approval, "overflow", media[1], launch=False)


def test_cancellation_only_stops_owned_ffmpeg_and_never_publishes(media, monkeypatch):
    jobs, _, _, job = approved_job(media)
    real_command = render_command

    def slow_command(edit, assets, output):
        command = real_command(edit, assets, output)
        command.insert(command.index("-i"), "-re")
        return command

    monkeypatch.setattr("mashup.extension.worker.render_command", slow_command)
    import threading

    worker = threading.Thread(target=run, args=(jobs.state,))
    worker.start()
    deadline = time.monotonic() + 5
    while jobs.get(job["jobId"])["state"] == "queued" and time.monotonic() < deadline:
        time.sleep(0.02)
    jobs.cancel(job["jobId"])
    worker.join(timeout=8)
    assert not worker.is_alive()
    assert jobs.get(job["jobId"])["state"] == "cancelled"
    with pytest.raises(ValueError, match="no published result"):
        jobs.result(job["jobId"])


def test_canonical_skill_catalog_is_read_only_and_rejects_escapes(tmp_path):
    root = tmp_path / "skills"
    skill = root / "original"
    skill.mkdir(parents=True)
    source = skill / "SKILL.md"
    original = (
        "---\nname: original\ndescription: Canonical guidance\n---\nHost executes this skill."
    )
    source.write_text(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("private outside file")
    (root / "escaped").symlink_to(outside)
    catalog = Skills([root])
    listed = catalog.list()
    assert len(listed) == 1
    assert listed[0]["name"] == "original"
    assert catalog.read(listed[0]["id"])["content"] == original
    assert source.read_text() == original
    with pytest.raises(ValueError, match="not found"):
        catalog.read("../outside/SKILL.md")


def test_render_deadline_fails_without_publishing_an_artifact(media, monkeypatch):
    jobs, _, _, job = approved_job(media)
    monkeypatch.setattr("mashup.extension.worker.MAX_RENDER_SECONDS", 0)
    run(jobs.state)
    assert jobs.get(job["jobId"])["state"] == "failed"
    assert "deadline" in jobs.get(job["jobId"])["error"]
    with pytest.raises(ValueError, match="no published result"):
        jobs.result(job["jobId"])


def test_real_stdio_mcp_discovery_and_render_survive_client_disconnect(media):
    mcp = pytest.importorskip("mcp")
    from mcp.client.stdio import stdio_client

    state, _, bg, fg, inputs = media
    client_environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    config = json.loads(
        subprocess.run(
            [
                sys.executable,
                "-m",
                "mashup.extension.mcp",
                "--state-dir",
                str(state),
                "--input-root",
                str(inputs),
                "--print-pace-config",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=client_environment,
            timeout=10,
        ).stdout
    )["mcpServers"]["mashup-media"]
    assert Path(config["command"]).is_absolute()
    params = mcp.StdioServerParameters(
        command=config["command"],
        args=config["args"],
        cwd=state.parent,
        env=client_environment,
    )

    async def request_sequence():
        async with stdio_client(params) as (read, write), mcp.ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {
                "media_capabilities",
                "asset_register",
                "render_start",
                "job_cancel",
                "skill_read",
            } <= {tool.name for tool in tools.tools}
            capabilities = await session.call_tool("media_capabilities", {})
            assert not capabilities.isError
            assert capabilities.content[0].text
            assert capabilities.structuredContent["rendersDetach"] is True
            registered = await session.call_tool(
                "asset_register",
                {
                    "path": str(inputs / "background.mp4"),
                    "rights": rights("Moving background").model_dump(),
                },
            )
            assert not registered.isError, registered
            assert registered.structuredContent["id"] == bg["id"]
            rejected = await session.call_tool(
                "render_plan",
                {"composition": {**composition(bg, fg).model_dump(), "shell": "unsafe"}},
            )
            assert rejected.isError
            proposed = await session.call_tool(
                "render_plan", {"composition": composition(bg, fg).model_dump()}
            )
            plan = proposed.structuredContent
            unapproved = await session.call_tool(
                "render_start",
                {
                    "plan_id": plan["planId"],
                    "idempotency_key": "missing-approval",
                },
            )
            assert unapproved.isError
            started = time.monotonic()
            queued = await session.call_tool(
                "render_start",
                {
                    "plan_id": plan["planId"],
                    "idempotency_key": "stdio-survival",
                    "approval": {
                        "status": "approved",
                        "approvedBy": "Fixture owner explicitly approved",
                        "planHash": plan["planHash"],
                    },
                },
            )
            assert time.monotonic() - started < 5
            assert not queued.isError, queued
            job_id = queued.structuredContent["jobId"]
        deadline = time.monotonic() + 30
        async with stdio_client(params) as (read, write), mcp.ClientSession(read, write) as session:
            await session.initialize()
            while time.monotonic() < deadline:
                status = await session.call_tool("job_status", {"job_id": job_id})
                if status.structuredContent["state"] in {"succeeded", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.1)
            assert status.structuredContent["state"] == "succeeded", status
            result = await session.call_tool("job_result", {"job_id": job_id})
            assert not result.isError
            assert Path(result.structuredContent["video"]["path"]).is_file()

    asyncio.run(request_sequence())
