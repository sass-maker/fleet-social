# Fleet Social status

## Why / What

Private Fleet social publishing hub based on CogSend. The approved first release adds Fleet project attribution, draft-only intake, review-bound approval, and accurate per-destination receipts.

## Dependencies

- CogSend upstream at `01ac9bed052f55d47bac084a4d6d71fd292dc743` (MIT).
- Cloudflare Workers, D1, and R2 for the eventual isolated deployment.
- Provider-owned credentials and account approval for any live canary.

## Timeline

- 2026-09-25: Private repository created and [first-release scope](https://github.com/sass-maker/fleet-social/issues/1) approved. Fleet variant passed 998 unit tests, Svelte check, lint, build, responsive browser review, and a local D1 backup/restore rehearsal. The canonical Fleet catalog records the private product as undeployed.
- 2026-09-26: The complete 54-case browser suite passed on a fresh local D1 instance after adapting the upstream scenarios to required project ownership and approval.

## Products

- One internal web instance for the Fleet owner.

## Features (implemented locally)

- Project-attributed drafts from the canonical active Fleet project list.
- Draft-only intake with a stable source reference and idempotent creation.
- Session-only review approval bound to saved content, media, variants, and destinations. Publishing, scheduling, retries, and scheduler attempts enforce it.
- Per-account outcomes and a paused uncertain state for possible provider acceptance, with owner reconciliation in Posts.

## Todo / Planned / Deferred / Blocked

- In progress: Fleet dossier refresh is waiting for the unrelated dirty Site Health worktree to be reconciled.
- Planned: isolated Worker, D1, and R2 deployment qualification and one approved live provider canary.
- Deferred: video, additional providers, AI generation, teams, public SaaS.
- Blocked for live deployment by missing isolated resource configuration and the repository rule against touching credentials, environment files, or production config. The upstream `wrangler.jsonc` still names CogSend resources and must not be used for Fleet deployment. No social account or platform permission has been verified.
