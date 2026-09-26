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

## Products

- One internal web instance for the Fleet owner.

## Features

- Project-attributed drafts from the canonical active Fleet project list.
- Draft-only intake with a stable source reference and idempotent creation.
- Session-only review approval bound to saved content, media, variants, and destinations. Publishing, scheduling, retries, and scheduler attempts enforce it.
- Per-account outcomes and a paused uncertain state for possible provider acceptance, with owner reconciliation in Posts.
- One owner YouTube channel, encrypted refresh credentials, private MP4 upload through resumable R2 chunks, and video ID, visibility, and URL receipts. A private live canary is verified; interrupted-upload recovery is covered by focused tests but has not been exercised against a live interruption.

## Todo / Planned / Deferred / Blocked

- In progress: verify an actual scheduled YouTube publish and machine draft intake. Fleet dossier refresh is waiting for the unrelated dirty Site Health worktree to be reconciled.
- Deferred: additional providers, AI generation, teams, public SaaS, and public YouTube uploads until the Google API project is eligible and the owner approves a public canary.
- Operational limit: the Google OAuth project is in Testing, so the channel's refresh token may expire after seven days. Commit `741ff6b` contains the feature locally, but a sandbox DNS block prevented pushing it to GitHub; the live Worker predates its final composer-copy update. The upstream `wrangler.jsonc` still names CogSend resources, so Fleet deployment uses its separate local configuration.
