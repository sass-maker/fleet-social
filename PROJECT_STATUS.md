# Fleet Social status

## Why / What

Private Fleet social publishing hub based on CogSend. The approved first release adds Fleet project attribution, draft-only intake, review-bound approval, and accurate per-destination receipts.

## Dependencies

- CogSend upstream at `01ac9bed052f55d47bac084a4d6d71fd292dc743` (MIT).
- Isolated Cloudflare Worker, D1, and R2, now deployed.
- Provider-owned credentials and owner approval for live publication.

## Timeline

- 2026-09-25: Private repository created and [first-release scope](https://github.com/sass-maker/fleet-social/issues/1) approved. Fleet variant passed 998 unit tests, Svelte check, lint, build, responsive browser review, and a local D1 backup/restore rehearsal. The canonical Fleet catalog records the private product as undeployed.
- 2026-09-26: The complete 54-case browser suite passed on a fresh local D1 instance after adapting the upstream scenarios to required project ownership and approval.
- 2026-09-26: The private Fleet Worker was deployed with Cloudflare Access, GitHub and Google sign-in. The owner's YouTube channel was connected using a separate Google OAuth client. An approved 10-second MP4 canary published privately as [video jhAAHDS1oME](https://www.youtube.com/watch?v=jhAAHDS1oME); Fleet Social's receipt and the YouTube page both show the private video. The latest full local check passed 1,007 tests, Svelte check, lint, and build before the canary.
- 2026-09-26: Public app, privacy, and terms pages plus explicit YouTube policy consent were implemented locally. OAuth start, YouTube verification, and upload now require current consent. Svelte check, lint, build, and 1,008 unit tests pass. These changes have not yet reached the live Worker.
- 2026-10-04: Added the owner's selected Editorial Planner, playable video review, local Mashup creation from ideas/products/source links, and Instagram provider support. Source selection now requires complete caption passages, ranks topic-word specificity and density, excludes long gaps and verbatim repeats, and reports when no matching passage fits. Fifteen focused Python regressions cover source selection and video preparation. Current release verification and remaining provider qualification are tracked in [#10](https://github.com/sass-maker/fleet-social/issues/10).
- 2026-10-09: Mashup moved into this repository as the local media module `tools/mashup/` (from `sass-maker/mashup` at `1adcf83`, [#13](https://github.com/sass-maker/fleet-social/issues/13)). The rehearsal adapter uses it by default; it stays out of the Worker build. Mashup's 507 Python tests, ruff, and an in-repo product render with a hash-matching `fleet.mashup-media-receipt.v1` pass. The public proof site, catalog retirement and repository archive remain owner cutover steps.
- 2026-10-25: Generator-neutral media intake. Any local, cloud or API generator (Mashup is one clipping agent) pushes a finished file with a `fleet.media-receipt.v1` receipt; the Worker checks size, type and SHA-256, keeps generator/source provenance for review, and treats a repeated push as the same file. An intake-only key can attach receipted media to its own unapproved draft. `npm run media:push` was verified against the isolated local Worker (create, verify, idempotent repeat, review display); no push to the deployed instance yet.
- 2026-10-25: Media intake deployed (Worker version `4e98c7d2`, migration 0019). Added scheduled generation: generators (local, cloud, API) and calendar slots that request media a lead time before release. Cloud and API generators get an HMAC-signed webhook from the scheduler tick with backoff; local generators claim due slots. A slot becomes ready when media arrives at its `slot:<id>` sourceRef. Both paths were verified on the isolated local Worker, including a real webhook receiver that checked the signature.

## Products

- One internal web instance for the Fleet owner.

## Features

- Project-attributed drafts from the canonical active Fleet project list.
- Draft-only intake with a stable source reference and idempotent creation.
- Session-only review approval bound to saved content, media, variants, and destinations. Publishing, scheduling, retries, and scheduler attempts enforce it.
- Per-account outcomes and a paused uncertain state for possible provider acceptance, with owner reconciliation in Posts.
- One owner YouTube channel, encrypted refresh credentials, private MP4 upload through resumable R2 chunks, and video ID, visibility, and URL receipts. A private live canary is verified; interrupted-upload recovery is covered by focused tests but has not been exercised against a live interruption.
- Month/week calendar and review queue with actual approval, scheduling and per-destination delivery states. The owner's timezone and selected calendar view/date survive review navigation.
- Local Mashup generation: editable original product/idea scenes, public licensed-source mashups, timed captions, source credits and hash-verified media receipts. Repeated identical completed plans reuse verified media and create a separate unapproved draft. Fresh generation takes time.
- Source passages use complete caption boundaries up to 24 seconds per source, with topic-word ranking and repetition penalties. Captionless inputs use the disclosed first-ten-second fallback. Caption quality and lexical matching still require editorial review; no semantic-model scoring is claimed.
- Instagram Creator/Business connection and Reels container processing, resumable state, encrypted credentials, and conservative handling of uncertain publication. The integration is implemented and covered by provider-state tests; real Meta app connection and publication remain unqualified.
- Generated media intake from any local, cloud or API generator through one receipt contract (`docs/api.md#generated-media`), with digest verification and provenance on review. Up to 95 MB per file. Calendar slots request generation ahead of release from a webhook or a claiming local agent (`docs/api.md#scheduled-generation`). Larger files need streaming publishing per provider, because every publish path buffers the file and caps video at 95 MB.
- Isolated local rehearsal with sample destinations, persistent local approvals/calendar data, real playable videos and local upload receipts. Rehearsal never contacts a publishing provider and is absent from the production Worker.

## Todo / Planned / Deferred / Blocked

- Pending: configure the owner's Meta application, complete Instagram OAuth and qualify an actual Reel. Verify current YouTube connection health, machine draft intake and an actual scheduled private YouTube upload. Local sample destinations do not establish live connections.
- Deferred: semantic/AI clip ranking, teams, public SaaS, and public YouTube uploads until the Google API project is eligible and the owner approves a public canary.
- Operational limit: the Google OAuth project was in Testing at the verified canary, so its refresh token may expire after seven days. Current consent, policy-page reachability and provider readiness must be checked before a new live upload. The upstream `wrangler.jsonc` names CogSend resources; Fleet deployment uses its existing isolated local configuration. Exact deployed version/commit and utility/authenticated route evidence are recorded in #10.
- Mashup rendering remains a local workflow. The deployed Worker accepts finished MP4s for review, approval and delivery; it does not run FFmpeg, download source programs or expose rehearsal authentication.
