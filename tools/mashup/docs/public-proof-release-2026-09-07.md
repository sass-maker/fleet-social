# Public proof release — 2026-09-07

The finished-example showcase at https://mashup.highsignal.app now serves real approved media instead of HTML fallbacks. This does not make the site a hosted clipping service or complete the operator pilot.

## Source and deployment

- Checked, clean, synced source: `9673d781026dad85855edb5d8ac0b6c291f8d53a`; [exact CI 34107001659](https://github.com/sass-maker/mashup/actions/runs/34107001659) passed.
- Re-ran `uv run --no-sync python scripts/check_public_proof.py output/public-proof-staging`: both approval receipts, four byte counts/hashes, MP4 signatures/video streams and WebVTT passed.
- Staging contains 26 files, 22,660,786 bytes. Largest file is 16,712,890 bytes. Static files match the existing build; editor and visual-lab are excluded.
- Authorized deployment used existing authenticated Wrangler: `wrangler pages deploy output/public-proof-staging --project-name=mashup --branch=main --commit-hash=9673d781026dad85855edb5d8ac0b6c291f8d53a`.
- Provider confirmed production/main deployment `837580c0-b324-4e6a-a53d-0fa3e974b748`, source `9673d78`: https://837580c0.mashup-a6h.pages.dev.
- Never replace this release with plain `web/dist`: that build omits generated media. Re-run the complete-bundle guard before every deployment.

### Guarded publisher

Run `pnpm deploy /absolute/path/to/complete-public-proof-bundle` from `web/`
through Fleet Workspace after the exact main commit passes CI. The publisher
refuses dirty or unpublished source, builds the current site, compares every
public static file to the supplied bundle, rejects operator routes and
unexpected files, and verifies both approved media/caption receipts with the
existing MP4/WebVTT guard before invoking the existing Mashup Pages target.
Generated media remains outside Git. A build alone cannot produce this bundle;
retain the approved media when staging new static files. After publication,
verify canonical asset hashes and both players; provider success is not playback
proof. The publisher uses Python's standard library and the already-used pinned
Wrangler CLI, without adding production dependencies.

## Hosted acceptance

Ordinary canonical public URLs for both MP4s and both VTTs were downloaded read-only and matched the approved receipts byte-for-byte and SHA-256-for-SHA-256. Exact hashes remain in the [recovery receipt](shareability-qualification-2026-09-07.md). Both public JSON receipts decode successfully, report approved status and retain licensed source provenance.

Fresh isolated Chromium contexts exercised the actual two page players at desktop 1440×1000 and mobile 390×844. Playback was muted for automation. All four attempts advanced beyond two seconds with 54–56 decoded frames, readyState 4, 1080×1920 and no media error. Durations were 47.15 and 13.16 seconds. English caption tracks loaded 11 and 4 cues. Neither viewport had document overflow.

The visible page explains the three-episode synthesis and compact single-idea example, links their receipts and sources, and states that editing, approvals and posting remain local operator responsibilities. Screenshots show actual footage and captions, not poster-only playback claims. No clipping, models, new source acquisition, credential changes or creator-channel publishing occurred.

- [Machine playback receipt](verification/2026-09-07-hosted-playback.json)
- [Desktop synthesis](verification/2026-09-07-desktop-0.png) · [Desktop compact example](verification/2026-09-07-desktop-1.png)
- [Mobile synthesis](verification/2026-09-07-mobile-0.png) · [Mobile compact example](verification/2026-09-07-mobile-1.png)

## Rollback and remaining scope

Prior production was `f6ed5844-568a-49d8-b1bf-b9835f3f9245`, source `60bae97`; it has the missing-media failure. The preserved media-bearing production target is `13e2bcd0-643d-4d2e-90a4-62e00584d783`, source `885c996`, at https://13e2bcd0.mashup-a6h.pages.dev. Fresh read-only requests verified both MP4s there. If required, select the desired prior production row in Cloudflare Pages → Mashup → Deployments → Rollback to this deployment. No rollback was performed. Installed Wrangler has no Pages rollback subcommand.

[Issue #11](https://github.com/sass-maker/mashup/issues/11) remains open: a creator-authorized 3–5 clip batch, 20–30 approved clip pilot, review-time and editorial-quality evidence remain incomplete. Local synthetic operator-authored export evidence does not prove automatic editorial selection or pilot success. This release restores public media playback. Subsequent visual review found duplicate default captions, so presentation qualification remains pending the follow-up below. Reconciliation: one open issue, zero open PRs, zero closures.

## Default-caption follow-up (prepared, not deployed)

Visual review found the default native VTT overlay duplicating captions already burned into both videos. The two HTML tracks now omit `default`; English VTT remains selectable. No media was regenerated or changed.

Astro check/build pass. The complete `output/public-proof-caption-staging/` bundle preserves all approved media hashes and excludes editor/visual-lab. In isolated Chromium, the local complete bundle was served through route interception to preserve its production-origin absolute asset URLs. Both videos played past two seconds on desktop/mobile with tracks disabled by default. Explicitly enabling each track loaded its 11/4 cues. The initial localhost-only check exposed cross-origin caption loading rather than a product failure; the origin-preserving test passed.

[Local mobile corrected captions](verification/2026-09-07-caption-local-mobile.png) and [local playback/optional-track assertions](verification/2026-09-07-caption-local.json). The approved next release must use the complete caption-staging bundle, retain deployment `837580c0-b324-4e6a-a53d-0fa3e974b748` as its immediate rollback, and verify ordinary public default-caption behavior before claiming presentation qualification.

## Caption follow-up deployed and verified

The approved complete caption-staging bundle deployed after exact source `40c03b061d56a21a23116630a4eb7afc9e5df3eb` [CI 34128659289](https://github.com/sass-maker/mashup/actions/runs/34128659289) passed. Provider confirmed production/main deployment `7f6d4f77-fd4c-4bdd-a722-15bc4fe35e66`, source `40c03b0`: https://7f6d4f77.mashup-a6h.pages.dev. The prior `837580c0-b324-4e6a-a53d-0fa3e974b748` is preserved for immediate rollback. No rollback or media regeneration occurred.

Ordinary hosted requests (no interception) again matched all four approved media/caption hashes. Fresh desktop and mobile contexts played both videos beyond two seconds with 54–55 decoded frames, no errors and the same 47.15/13.16 second durations. Both native tracks were disabled by default; explicit activation loaded 11/4 cues. Visually inspected mobile screenshots show one burned-in caption layer, with no duplicate native overlay. The public finished-proof showcase now passes this bounded playback and presentation qualification; #11 remains open for the operator pilot.

- [Final hosted assertions](verification/2026-09-07-caption-hosted.json)
- [Desktop synthesis](verification/2026-09-07-caption-hosted-1440-0.png) · [Desktop compact](verification/2026-09-07-caption-hosted-1440-1.png)
- [Mobile synthesis](verification/2026-09-07-caption-hosted-390-0.png) · [Mobile compact](verification/2026-09-07-caption-hosted-390-1.png)

Earlier screenshots intentionally retain the initial duplicate-caption finding; these final captures are the accepted presentation evidence. All isolated browsers and the temporary localhost server were closed. Both complete staging bundles remain under ignored output for operator continuity; no generated MP4 or VTT was committed.
