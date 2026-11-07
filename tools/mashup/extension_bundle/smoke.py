#!/usr/bin/env python3
"""Verify an installed MCP runtime in new scratch state, optionally rendering a fixture."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import mashup


async def verify(state: Path, render: bool) -> dict:
    if state.exists():
        raise ValueError("Verification requires new scratch state, never existing operator state")
    if not Path(mashup.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError("Mashup must load from the installed virtual environment")
    state.mkdir(mode=0o700, parents=True)
    inputs = state / "inputs"
    inputs.mkdir()
    environment = {"PATH": os.environ.get("PATH", os.defpath), "PYTHONNOUSERSITE": "1"}
    parameters = StdioServerParameters(
        command=str(Path(sys.executable).absolute()),
        args=[
            "-m",
            "mashup.extension.mcp",
            "--state-dir",
            str(state / "registry"),
            "--input-root",
            str(inputs),
            "--skills-root",
            str(Path(sys.prefix).parent / "resources"),
        ],
        cwd=str(state),
        env=environment,
    )

    async def call(session, name, arguments):
        response = await asyncio.wait_for(session.call_tool(name, arguments), timeout=20)
        if (
            response.isError
            or not response.structuredContent
            or not any(getattr(item, "text", "") for item in response.content)
        ):
            raise ValueError("MCP operation failed: " + name)
        return response.structuredContent

    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await asyncio.wait_for(session.initialize(), timeout=10)
        tools = {
            tool.name for tool in (await asyncio.wait_for(session.list_tools(), timeout=10)).tools
        }
        if not {"media_capabilities", "render_start", "job_cancel", "skill_read"} <= tools:
            raise ValueError("Installed server is missing required tools")
        capabilities = await call(session, "media_capabilities", {})
        templates = await call(session, "templates_list", {})
        skills = await call(session, "skills_list", {})
        guide = next((item for item in skills["skills"] if item["name"] == "mashup-media"), None)
        if guide is None:
            raise ValueError("Installed usage guide is missing")
        await call(session, "skill_read", {"skill_id": guide["id"]})
        if capabilities["publishes"] or capabilities["arbitraryExecution"]:
            raise ValueError("Unexpected installed execution capabilities")
        if not any(item["id"] == "character-overlay@1" for item in templates["templates"]):
            raise ValueError("Installed template is missing")
        result = {
            "installedModule": str(Path(mashup.__file__).resolve()),
            "toolCount": len(tools),
            "discoveryVerified": True,
            "renderVerified": False,
        }
        if not render:
            return result
        background = inputs / "background.mp4"
        foreground = inputs / "foreground.mov"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=96x64:rate=24:duration=1",
                "-an",
                "-c:v",
                "libx264",
                str(background),
            ],
            env=environment,
            check=True,
            timeout=15,
        )
        frames = bytearray()
        for index in range(6):
            for y in range(32):
                for x in range(32):
                    color = bytes((255, 0, 0, 255) if index < 3 else (0, 255, 0, 255))
                    frames.extend(color if 8 <= x < 24 and 8 <= y < 24 else bytes(4))
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
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
            ],
            input=bytes(frames),
            env=environment,
            check=True,
            timeout=15,
        )
        asset_ids = []
        for path in (background, foreground):
            asset = await call(
                session,
                "asset_register",
                {
                    "path": str(path),
                    "rights": {
                        "title": "Original procedural verification fixture",
                        "creator": "Mashup verification",
                        "sourceUrl": "urn:mashup:fixture",
                        "license": "creator-owned",
                        "licenseUrl": "urn:mashup:fixture:rights",
                        "rightsConfirmed": True,
                    },
                },
            )
            asset_ids.append(asset["id"])
        plan = await call(
            session,
            "render_plan",
            {
                "composition": {
                    "background": asset_ids[0],
                    "foreground": asset_ids[1],
                    "duration": 1.0,
                    "preset": "landscape",
                    "fps": 24,
                    "loop": True,
                    "motion": "bob",
                }
            },
        )
        await call(session, "render_validate", {"plan_id": plan["planId"]})
        job = await call(
            session,
            "render_start",
            {
                "plan_id": plan["planId"],
                "idempotency_key": "installed-fixture",
                "approval": {
                    "status": "approved",
                    "approvedBy": "Installer fixture owner",
                    "planHash": plan["planHash"],
                },
            },
        )
    deadline = time.monotonic() + 30
    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await asyncio.wait_for(session.initialize(), timeout=10)
        while time.monotonic() < deadline:
            status = await call(session, "job_status", {"job_id": job["jobId"]})
            if status["state"] in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.1)
        if status["state"] != "succeeded":
            raise ValueError("Installed render failed: " + json.dumps(status))
        artifact = await call(session, "job_result", {"job_id": job["jobId"]})
    video = Path(artifact["video"]["path"])
    if hashlib.sha256(video.read_bytes()).hexdigest() != artifact["video"]["sha256"]:
        raise ValueError("Installed render artifact hash mismatch")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-f", "null", "-"],
        env=environment,
        check=True,
        timeout=15,
    )
    return {**result, "renderVerified": True, "artifact": artifact}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(verify(args.state_dir.absolute(), args.render)), indent=2))


if __name__ == "__main__":
    main()
