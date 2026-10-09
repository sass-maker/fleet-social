"""Qualify existing local planning/rendering with original silent text-card inputs.

No speech is generated. The supplied SRT describes authored screen cards;
this is an integration proof, not a transcription or creator-pilot evaluation.
Run prepare, then plan or operator-plan. Inspect the proposed edit before render.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "plan", "operator-plan", "render"])
    parser.add_argument("--output", type=Path, default=Path("output/synthetic-qualification"))
    args = parser.parse_args()
    # This qualification may use only already-cached local models and no credentials.
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        MASHUP_AI_API_KEY="",
        MASHUP_EMBED_BACKEND="local",
        MASHUP_CHAT_BACKEND="local",
    )
    from mashup.agent import failure, run_agent

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    workdir = root / "work"

    def operation(name: str, inputs: dict) -> dict:
        request = {
            "schema": "fleet.video-agent-operation.v1",
            "product": "mashup",
            "operation": name,
            "input": inputs,
        }
        validated = run_agent({**request, "validateOnly": True})
        (root / f"{name}-validation.json").write_text(json.dumps(validated, indent=2) + "\n")
        try:
            result = run_agent(request)
        except Exception as error:
            (root / f"{name}-failure.json").write_text(
                json.dumps(failure(request, error), indent=2) + "\n"
            )
            raise
        (root / f"{name}-result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(name, result["state"], flush=True)
        return result

    if args.phase == "prepare":
        archive = root / "archive"
        archive.mkdir(exist_ok=True)
        sources = [
            (
                "01-problem",
                "Find the problem",
                "0x12304a",
                [
                    "A signup form asked for a phone number.",
                    "Ten fictional visitors reached that field.",
                    "Eight left because the number was optional.",
                    "The unnecessary field caused the drop-off.",
                ],
            ),
            (
                "02-experiment",
                "Test one change",
                "0x244c40",
                [
                    "A signup experiment removed an optional phone field.",
                    "Two identical groups saw different forms.",
                    "The shorter form got more completions.",
                    "Removing one field reduced signup friction.",
                ],
            ),
            (
                "03-decision",
                "Review the result",
                "0x482844",
                [
                    "A team reviewed its signup experiment.",
                    "The shorter form helped more visitors finish.",
                    "The team kept the shorter form.",
                    "The decision followed measured results.",
                ],
            ),
        ]
        files = []
        for name, title, color, lines in sources:
            source = archive / f"{name}.mp4"
            subtitle = source.with_suffix(".srt")
            subtitle.write_text(
                "\n".join(
                    f"{i + 1}\n00:00:{i * 3 + 1:02d},000 --> "
                    f"00:00:{(i + 1) * 3 + 1:02d},000\n{text}\n"
                    for i, text in enumerate(lines)
                )
            )
            title_file = archive / f"{name}.txt"
            title_file.write_text(title)
            visual = (
                f"drawtext=textfile='{title_file}':fontcolor=white:fontsize=60:"
                "x=(w-tw)/2:y=h/2-40,drawtext=text='FICTIONAL EXAMPLE - NO SPEECH':"
                "fontcolor=white:fontsize=24:x=(w-tw)/2:y=h/2+50"
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={color}:s=1280x720:r=24:d=14",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=stereo",
                    "-vf",
                    visual,
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-t",
                    "14",
                    str(source),
                ],
                check=True,
            )
            files.append(
                {
                    "filename": source.name,
                    "source_url": source.as_uri(),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            )
        provenance = {
            "creator": "Original synthetic cards authored by Codex for user-authorized local QA",
            "license": "Creator-owned synthetic local QA material",
            "license_url": "https://github.com/sass-maker/mashup",
            "feed": archive.as_uri(),
            "files": files,
        }
        (root / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        operation(
            "ingest", {"workdir": str(workdir), "inputDir": str(archive), "transcribe": False}
        )
        operation("enrich", {"workdir": str(workdir), "concurrency": 1})
        operation("embed", {"workdir": str(workdir)})
        print("Prepared source archive; run plan or operator-plan next.")
    elif args.phase == "plan":
        operation(
            "plan",
            {
                "workdir": str(workdir),
                "prompt": (
                    "Start with the signup problem, then test one change, "
                    "finish with a measured decision."
                ),
                "duration": 36,
                "variants": 1,
                "snap": False,
                "output": str(root / "edls"),
            },
        )
    elif args.phase == "operator-plan":
        from mashup.config import load_config
        from mashup.models import ScoreTerms
        from mashup.pipeline import planning_session, result_to_edl
        from mashup.plan.planner import PlanResult, rescore
        from mashup.render import save_edl

        cfg = load_config(workdir)
        prompt = (
            "Start with the signup problem, then test one change, finish with a measured decision."
        )
        with planning_session(prompt, cfg, target=36, pool=10, editorial=False) as session:
            sequence = sorted(session.store.get_segments(), key=lambda segment: segment.source_id)
            result = rescore(
                PlanResult(
                    "chronological",
                    sequence,
                    ScoreTerms(),
                    0,
                    [
                        "Operator-authored synthetic QA order: problem, experiment, decision.",
                        (
                            "Automatic podcast boundary review rejected these silent cards; "
                            "no automated editorial acceptance is claimed."
                        ),
                    ],
                ),
                session.ctx,
                session.retriever.pairwise,
            )
            edl = result_to_edl(
                result,
                session.request,
                cfg,
                session.store,
                target=36,
                snap=False,
                calibration=session.ctx.calibration,
            )
            save_edl(edl, root / "edls/chronological.json")
        operation("validate-edl", {"edlPath": str(root / "edls/chronological.json")})
        operation(
            "export-podcast-edit",
            {
                "edlPath": str(root / "edls/chronological.json"),
                "provenancePath": str(root / "provenance.json"),
                "editId": "synthetic-signup-qa",
                "output": str(root / "proposed-edit.json"),
            },
        )
    else:
        operation(
            "export-podcast-edit",
            {
                "edlPath": str(root / "edls/chronological.json"),
                "provenancePath": str(root / "provenance.json"),
                "editId": "synthetic-signup-qa",
                "approval": "approved",
                "approvedBy": "Codex reviewed this synthetic EDL under user-authorized local QA",
                "output": str(root / "approved-edit.json"),
            },
        )
        operation(
            "render",
            {
                "podcastEditPath": str(root / "approved-edit.json"),
                "output": str(root / "signup-lesson.mp4"),
                "workdir": str(workdir),
                "subtitles": "burn",
                "profile": "social",
                "watermarkText": "SYNTHETIC QA",
            },
        )
        probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(root / "signup-lesson.mp4"),
                ]
            )
        )
        (root / "probe.json").write_text(json.dumps(probe, indent=2) + "\n")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(root / "signup-lesson.mp4"), "-f", "null", "-"],
            check=True,
        )
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        operation(
            "media-receipt",
            {
                "podcastEditPath": str(root / "approved-edit.json"),
                "videoPath": str(root / "signup-lesson.mp4"),
                "captionsPath": str(root / "signup-lesson.srt"),
                "durationSeconds": float(probe["format"]["duration"]),
                "width": video["width"],
                "height": video["height"],
                "output": str(root / "media-receipt.json"),
            },
        )
        print(root / "signup-lesson.mp4")


if __name__ == "__main__":
    main()
