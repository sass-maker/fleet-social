# Mashup helper agent instructions

This is the local Mashup media module inside Fleet Social (`tools/mashup/`).
Also follow the repository root `AGENTS.md` and, inside the Fleet workspace,
the shared instructions at `../../../AGENTS.md`.

## Runtime

- Python 3.11+ managed with uv.
- Astro + React editor under `web/`, managed with pnpm.
- FFmpeg and SQLite remain local runtime dependencies.

## Verify

```bash
uv sync
uv run python -m pytest -q
uv run ruff check .
uv run ruff format --check .
cd web && pnpm install && pnpm check && pnpm build
```

## Agent interface

- Discover capabilities with a `manifest` request to `uv run mashup agent`.
- Send one `fleet.video-agent-operation.v1` JSON object on stdin or with
  `--request`; stdout is one result envelope and progress remains structured.
- Use `validateOnly: true` before mutation or rendering. Render requires an
  approved `fleet.podcast-edit.v1`; finished artifacts use an operation-linked
  `fleet.mashup-media-receipt.v1`.
- The interface rejects unknown fields, commands, code, executables, and
  plugins. Mashup never publishes media.

## Boundaries

- This directory is the canonical home of Mashup. It was consolidated from
  `sass-maker/mashup` at `1adcf83` (fleet-social#13); track work in Fleet
  Social's issues.
- Mashup runs locally only. The hosted Worker must never import or bundle it;
  Fleet Social receives finished MP4s with a verified
  `fleet.mashup-media-receipt.v1`, and publishing approval stays in Fleet
  Social.
- Keep Reel Pipeline integration at the finished-media receipt boundary. Do
  not import Reel Pipeline modules, read its state, or depend on its paths.
- Only creator-owned, appropriately licensed, or public-domain filmed and
  photographic media may enter an edit. Preserve provenance.
- Podcast feed acquisition (`mashup feed`, `mashup fetch-episode`) enforces
  that at the point of download: an unrecognised or `-nd` licence refuses,
  and `--i-have-rights` is recorded when a creator overrides it.
- Procedural non-photoreal motion, typography, diagrams, shaders, and ASCII are
  allowed. Synthetic speech, voice cloning, and deceptive photoreal footage
  are not.
- Keep transcription, enrichment, embedding, boundary review, and render
  intermediates resumable.
- Keep all eight score terms separate and surfaced in every exported edit.
- Do not copy or commit `archive/`, `.mashup/`, `output/`, model payloads,
  credentials, or generated artifacts.
- Existing operator workdirs remain external data. Never move or delete them
  automatically when source ownership changes.

## Kept in sync with on-record

`on-record/` (High Signal Podcasts) transcribes podcast audio the same way this
project does: `whisperkit-cli` over a 16 kHz mono WAV produced by ffmpeg. The
logic is duplicated on purpose — the two products have different rights
postures and release cycles, and neither should be able to break the other —
but duplicated code drifts silently, so treat these as a pair.

| here | there |
|---|---|
| `src/mashup/ingest/transcribe.py` | `python/ingest/src/on_record_ingest/transcripts/whisper_local.py` |

Known differences, both deliberate:

- This file is the more careful of the two: two backends resolved lazily, and
  a model directory from `MASHUP_WHISPERKIT_MODEL` so nothing downloads
  unasked. on-record pins one backend and one path.
- on-record additionally diarizes, using `whisperkit-cli --diarization` with
  the expected speaker count passed in, then assigns each caption cue to the
  turn it overlaps most. Mashup has no speaker turns today; if clip planning
  ever needs to tell a host from a guest, that is where the working version
  lives.

When you change transcription in either repo — model, flags, audio conversion,
timeout, cleanup — read the other file and say in the commit message whether
the change applies there too. If it does and you are not making it, note it in
that repo's issues rather than leaving the two to drift.
