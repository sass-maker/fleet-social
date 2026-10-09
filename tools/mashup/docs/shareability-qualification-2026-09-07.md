# Shareability qualification — 2026-09-07

The public-media failure below was repaired by the later approved
[production release](public-proof-release-2026-09-07.md). The diagnosis and local
recovery evidence remain as historical context.

## Public proof diagnosis and prepared recovery

The current public video URLs both return HTTP 200 **HTML**, not media:

- `https://mashup.highsignal.app/media/survive-technology-final.mp4`
- `https://mashup.highsignal.app/media/operators-final.mp4`

Their response bodies are the same 14,246-byte homepage as `/`, SHA-256
`c0eeca49eff988c26f2c532efbcb44355036545e2ba87191a266330401778a7a`.
`Content-Type` is `text/html; charset=utf-8`. This explains the previous
readyState 0 / time 0 playback failure: the media assets were missing from the
static deployment. HEAD success alone was misleading. The ordinary local web
build also lacks `media/`; generated proof files are intentionally not in Git.

The prior immutable deployment at
`https://13e2bcd0.mashup-a6h.pages.dev` still serves the original approved media.
Both MP4s and both VTTs were recovered locally and verified byte-for-byte
against the existing approved receipts:

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| Primary MP4 | 16,712,890 | `011e8af97ce9e6c4cfe6b02f61816aad47ef7a535dcca1a64b476f2477abd5d3` |
| Compact MP4 | 4,811,653 | `c43cb6e1fbb8e677b923e5d43c682db27afc33426df9287be4545b64628c7106` |
| Primary VTT | 1,017 | `61d34f853a281fb2d84c074711d509771ab08988720977215b567cb92e3a9f2e` |
| Compact VTT | 270 | `74c77d429add7a6bc9a16e433ff41a2e36b02e62b704b4e707d5eb4c31780655` |

The prepared bundle is retained locally at `output/public-proof-staging/`.
It combines the current static build and approved media/receipts, excluding
`editor/` and `visual-lab/`. The new read-only check verifies approval, safe
asset paths, exact bytes/hashes, MP4 signatures/video streams, and WebVTT:

```bash
rtk uv run python scripts/check_public_proof.py output/public-proof-staging
```

That bundle passes. Checking the plain `web/dist` fails for the missing MP4.
The check is an explicit operator step; this task did not change production
configuration or execute a deploy. A future approved deployment must use the
complete checked staging bundle rather than the ordinary build alone.

An isolated, already-cached Chrome for Testing played both recovered MP4s:
primary duration 47.15s, currentTime 2.617s, 69 decoded frames; compact duration
13.16s, currentTime 2.597s, 69 decoded frames. Both reported readyState 4,
1080×1920, and no media error. These are local recovery receipts, not a claim
that the public website is repaired. Public deployment and live replay remain.

## Local synthetic archive qualification

`scripts/qualify_synthetic_archive.py` creates three original silent text-card
recordings and supplied captions. Every card says **FICTIONAL EXAMPLE — NO
SPEECH**. No synthetic speech, third-party source acquisition, model download,
provider request, paid generation, or publication is involved.

Actual existing stages ran: media probing and ingestion, segmentation, cached
local Qwen enrichment, cached BGE embedding, operator-authored EDL rescoring,
proposed edit validation, reviewed synthetic approval, social rendering,
full FFmpeg decoding, and operation-linked media receipt creation. Mutating
agent operations were validated before execution.

The automatic `plan` operation **failed** the podcast boundary-review gate:
all three silent-card anchors lacked an accepted cold opening. Its verdicts
were preserved. The explicit `operator-plan` path instead uses Mashup's
existing human-edit rescoring and EDL export functions. This is not evidence
of automatic editorial selection or transcription quality.

The reviewed plan cuts 1–13 seconds from each 14-second source, orders problem
→ experiment → decision, preserves every caption, and exports all eight score
terms. The actual score was 0.73994; context completeness remains 0.5 and is
not inflated to indicate perfect editorial confidence.

Retained local output: `output/synthetic-qualification/signup-lesson.mp4`.

- H.264/AAC, 1080×1920, 24 fps, 864 video frames; 36.0s video.
- 329,335 bytes; SHA-256
  `c3f1635bd89ecc9a7152d5a8892ee730ab83a262ecaf101725e2ecc1e403c3dc`.
- Full FFmpeg decode passed. Isolated Chrome playback reached 2.509s with
  63 decoded frames, readyState 4, and no media error.
- Frames at 4s, 16s, and 28s were inspected: captions, source timecodes,
  synthetic watermark, and fitted source cards are readable and separate.
- EDL, proposed/approved edit, source provenance/hashes, model review cache,
  validation/results, media receipt, stream probe, frames, and playback
  receipt remain beside the local output. Generated artifacts are not committed.

Reproduction requires existing local model caches and FFmpeg with libass.
On this Mac, the already-installed `/opt/homebrew/opt/ffmpeg-full/bin` was
placed first on the command's PATH; the default FFmpeg lacks libass. The
script forces model libraries offline and disables dotenv/provider credentials.

```bash
rtk uv run python scripts/qualify_synthetic_archive.py prepare
rtk uv run python scripts/qualify_synthetic_archive.py plan
# If automatic editorial review rejects the cards, retain that failure.
rtk uv run python scripts/qualify_synthetic_archive.py operator-plan
# Inspect the EDL, provenance and proposed edit before this explicit QA approval.
rtk uv run python scripts/qualify_synthetic_archive.py render
```

## Checks and task reconciliation

502 Python tests passed, including two missing/HTML-media regressions. Ruff
check/format and Astro check/build passed; Astro reported its existing large
VisualLab chunk warning. No production dependencies were added or upgraded.

GitHub reconciliation found **1 open issue (#11), 0 open PRs, 0 closures**.
The Startups collection/batch/social-profile implementation exists and its
focused tests pass, but the real operator pilot remains incomplete. One
synthetic operator-authored export does not establish 20–30 approved creator
clips, review speed, repeatable quality, retention, or sharing performance.
