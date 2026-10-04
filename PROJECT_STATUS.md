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
- Isolated local rehearsal with sample destinations, persistent local approvals/calendar data, real playable videos and local upload receipts. Rehearsal never contacts a publishing provider and is absent from the production Worker.

## Todo / Planned / Deferred / Blocked

- Pending: configure the owner's Meta application, complete Instagram OAuth and qualify an actual Reel. Verify current YouTube connection health, machine draft intake and an actual scheduled private YouTube upload. Local sample destinations do not establish live connections.
- Deferred: semantic/AI clip ranking, teams, public SaaS, and public YouTube uploads until the Google API project is eligible and the owner approves a public canary.
- Operational limit: the Google OAuth project was in Testing at the verified canary, so its refresh token may expire after seven days. Current consent, policy-page reachability and provider readiness must be checked before a new live upload. The upstream `wrangler.jsonc` names CogSend resources; Fleet deployment uses its existing isolated local configuration. Exact deployed version/commit and utility/authenticated route evidence are recorded in #10.
- Mashup rendering remains a local workflow. The deployed Worker accepts finished MP4s for review, approval and delivery; it does not run FFmpeg, download source programs or expose rehearsal authentication.
