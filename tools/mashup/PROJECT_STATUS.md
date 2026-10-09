# Mashup — Project Status

Last updated: 2026-09-07

## Why / What

Mashup is an independent, local-first Fleet helper that turns creator-owned,
licensed, or public-domain podcast and video archives into coherent,
inspectable edits. It owns archive analysis, structure-aware planning,
approval, provenance, and multi-clip rendering.

## Dependencies

### External

- Python 3.11+, uv, SQLite, and FFmpeg.
- Optional local MLX, WhisperKit, Torch, and Transformers model runtimes.

### Fleet

- No runtime dependency on Reel Pipeline.
- Completed media may be handed to consumers through
  `fleet.mashup-media-receipt.v1`.

## Timeline

- **2026-09-07 public release:** deployed the complete approved proof bundle at
  source `9673d781026dad85855edb5d8ac0b6c291f8d53a`; canonical public MP4/VTT
  bytes match all four approved hashes. Both videos play with captions on
  desktop/mobile. Public source receipts remain available. This qualifies the
  finished-proof showcase, not the real operator pilot in #11.
  A follow-up at `40c03b0` removes duplicate default VTT overlays while
  preserving selectable tracks; fresh hosted desktop/mobile playback and
  presentation checks pass. [Release receipt](docs/public-proof-release-2026-09-07.md).

- **2026-09-07:** diagnosed missing public MP4s being served as successful HTML
  fallbacks; recovered the original approved videos/captions with exact receipt
  hashes into a complete local staging bundle. Qualified one 36-second synthetic
  operator-authored archive edit with real local enrichment/embeddings, eight
  scores, approval, captions, render, and advancing browser playback. Automatic
  podcast boundary acceptance, the real creator pilot, and public redeployment
  remain unqualified. See [receipts](docs/shareability-qualification-2026-09-07.md).

- **2026-08-30:** added the podcast RSS front door — feed resolution with
  pagination, episode listing and selection, and a licence-gated, hash-validated
  episode cache with acquisition records. The TTS half of the same issue is
  deliberately not built; the synthetic-speech boundary is unchanged and remains
  an owner decision.
- **2026-08-28:** released the first public proof surface around the strongest
  Startups result: a source-faithful 47-second argument assembled from four
  beats across three Creative Commons podcast episodes, with a compact
  13-second comparison, browser captions, and inspectable media receipts.
- **2026-08-27:** added the bounded Startups clipping desk pilot: checked-in
  category angles, distinct 3–5 short batches, an explicit 1080×1920 render
  profile, static local review, and strict agent-side batch planning.
- **2026-08-20:** restored the standalone repository as Mashup's canonical
  source, preserving the complete local-first runtime and making the finished
  media receipt the only Fleet/Reel integration boundary.
- **2026-08-09:** added the strict non-interactive `mashup agent` contract for
  capability discovery, resumable stages, approved renders, and
  operation-linked finished-media receipts.
- **2026-08-09:** extracted Mashup from Reel Pipeline into an independently
  owned helper with a finished-media receipt boundary.

## Products

- Local Mashup CLI and loopback editorial interface.
- Static [public proof site](https://mashup.highsignal.app) for approved finished
  media; it does not host the archive, editor, rendering pipeline, accounts,
  uploads, or publishing.

## Features (shipped)

- Podcast RSS acquisition: feed resolution and pagination, episode listing and
  selection, a fail-closed rights gate, and an idempotent, resumable episode
  cache with `fleet.mashup-episode-acquisition.v1` records.
- Resumable archive ingestion, transcription, enrichment, embedding, boundary
  review, planning, approval, and multi-clip rendering.
- Strict `fleet.podcast-edit.v1` editorial contract.
- Source-rights, provenance, source-hash, and non-repetition validation.
- Versioned finished-media receipts for decoupled downstream consumption.
- Machine-readable agent manifest and operations with strict input decoding,
  stable errors, structured progress, approval gates, and no arbitrary code.
- Category-led Startups presets, non-overlapping short batches, vertical social
  rendering, and a standalone browser review manifest with local review state.
- Public proof showcase with responsive source screening, an argument map,
  independent score evidence, episode attribution, captions, and media receipts.

## Work queue

Open work is tracked in [GitHub Issues](https://github.com/sass-maker/mashup/issues).
