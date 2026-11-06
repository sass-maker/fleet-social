from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

from mashup.extension.assets import MEDIA_FORMATS, Assets, probe
from mashup.extension.jobs import MAX_RENDER_SECONDS, Jobs
from mashup.extension.models import PRESETS
from mashup.extension.templates import validate
from mashup.media_receipt import build_media_receipt, save_media_receipt

MAX_OUTPUT_BYTES = 96 * 1024 * 1024


def render_command(edit: dict, assets: Assets, output: Path) -> list[str]:
    composition = validate(edit, assets)
    if edit.get("approval", {}).get("status") != "approved":
        raise ValueError("Template rendering requires an explicitly approved edit")
    background = assets.get(composition.background)
    foreground = assets.get(composition.foreground)
    width, height = PRESETS[composition.preset]
    fps = composition.fps
    command = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-threads",
        "2",
        "-filter_complex_threads",
        "1",
    ]
    for index, asset in enumerate((background, foreground)):
        command += ["-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_FORMATS]
        if asset["kind"] == "image":
            command += ["-loop", "1", "-framerate", str(fps)]
        elif index == 0 or composition.loop:
            command += ["-stream_loop", "-1"]
        command += ["-i", str(assets.path(asset))]
    x = f"{composition.x:g}*W-w/2"
    y = f"{composition.y:g}*H-h/2"
    if composition.motion in {"orbit", "slide"}:
        x += "+0.06*W*sin(2*PI*t/2)"
    if composition.motion in {"bob", "orbit"}:
        y += "+0.04*H*sin(2*PI*t/1.5)"
    graph = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps},setpts=PTS-STARTPTS[bg];"
        f"[1:v]scale={max(2, int(width * composition.width) // 2 * 2)}:-2,"
        f"setsar=1,fps={fps},format=rgba,setpts=PTS-STARTPTS[fg];"
        f"[bg][fg]overlay=x='{x}':y='{y}':format=auto:eof_action=pass:shortest=0,"
        "format=yuv420p[out]"
    )
    command += ["-filter_complex", graph, "-map", "[out]"]
    if background["media"]["hasAudio"]:
        command += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"]
    else:
        command += ["-an"]
    return command + [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-threads",
        "2",
        "-t",
        f"{composition.duration:g}",
        "-fs",
        str(MAX_OUTPUT_BYTES),
        "-movflags",
        "+faststart",
        str(output),
    ]


def render_job(jobs: Jobs, job: dict) -> dict:
    assets = Assets(jobs.state, [])
    edit = job["edit"]
    composition = validate(edit, assets)
    directory = jobs.state / "jobs" / job["id"]
    directory.mkdir(parents=True, exist_ok=True)
    partial = directory / "render.partial.mp4"
    log = directory / "render.log"
    started = time.monotonic()
    with log.open("wb") as stderr:
        child = subprocess.Popen(
            render_command(edit, assets, partial),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            close_fds=True,
        )
        try:
            while child.poll() is None:
                if jobs.cancelled(job["id"]):
                    raise ValueError("Render cancelled")
                if time.monotonic() - started > MAX_RENDER_SECONDS:
                    raise ValueError("Local render exceeded its 180 second deadline")
                if log.stat().st_size > 1024 * 1024 or (
                    partial.exists() and partial.stat().st_size > MAX_OUTPUT_BYTES
                ):
                    raise ValueError("Local render exceeded its output budget")
                time.sleep(0.05)
            if child.returncode:
                raise ValueError("Local FFmpeg render failed; inspect the job's local render.log")
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
    if jobs.cancelled(job["id"]):
        raise ValueError("Render cancelled")
    info = probe(partial)
    width, height = PRESETS[composition.preset]
    if (info["width"], info["height"]) != (width, height) or abs(
        info["duration"] - composition.duration
    ) > 2 / composition.fps:
        raise ValueError("Rendered media does not match the approved dimensions or duration")
    if not 0 < partial.stat().st_size <= MAX_OUTPUT_BYTES:
        raise ValueError("Rendered media does not meet the output size budget")
    video = directory / "video.mp4"
    os.replace(partial, video)
    receipt = build_media_receipt(
        edit,
        video_path=video,
        duration_seconds=info["duration"],
        width=width,
        height=height,
        recipe_id=composition.template,
        runtime_revision="mashup-extension@0.1.0",
        operation={
            "schema": "fleet.video-agent-operation.v1",
            "id": job["id"],
            "requestHash": job["requestHash"],
            "state": "completed",
        },
    )
    receipt["template"] = edit["template"]
    receipt_path = directory / "media-receipt.json"
    save_media_receipt(receipt, receipt_path)
    return {
        "video": {**receipt["output"]["video"], "uri": video.as_uri()},
        "receipt": {"path": str(receipt_path), "uri": receipt_path.as_uri()},
        "durationSeconds": info["duration"],
        "width": width,
        "height": height,
        "planHash": edit["template"]["planHash"],
    }


def run(state: Path) -> None:
    jobs = Jobs(state)
    while job := jobs.claim():
        try:
            result = render_job(jobs, job)
            jobs.finish(job["id"], result=result)
        except Exception as exc:
            jobs.finish(job["id"], error=str(exc)[:1200])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    run(parser.parse_args().state_dir)
