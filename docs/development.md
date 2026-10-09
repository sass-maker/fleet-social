# Development

Local setup, the checks that must pass, and what the code expects.

## Getting set up

Clone it — your fork works too — then:

```sh
git clone https://github.com/deepakness/cogsend.git
cd cogsend
npm install
cp .dev.vars.example .dev.vars
npm run db:seed:local
npm run dev
```

`.dev.vars` needs `APP_ENCRYPTION_KEY` (`openssl rand -hex 32`) — the only secret
the app requires. `AUTH_SECRET` and `SCHEDULER_SECRET` are derived from it unless
you set them, and `APP_URL` is taken from the request when it is unset.

Local development needs an account too, and the app will not create one: the
single account is written into D1 by `npm run setup` on a real deployment, and by
`npm run db:seed:local` for your local database. Run it once, then sign in at
http://localhost:5173 with the credentials it prints (`--password` gives you one
you can remember; `--reset` replaces an existing local account). It applies the
local migrations first, so `npm run db:migrate:local` is only needed on its own
when you add a migration.

The first sign-in asks you to enrol an authenticator (Google Authenticator or any
TOTP app); save the backup codes. Set `SKIP_TOTP=1` in `.dev.vars` to skip that
while developing — it is honored only while the instance resolves to a localhost
URL, so it can never weaken a deployment. Remove it and restart to go back to
real 2FA.

`npm run dev` ticks due posts every 30 seconds automatically, and
`npm run doctor` reports what it finds — locally or against a deployment — with
what to do about it.

## Checks

| Command                           | What it does                                         |
| --------------------------------- | ---------------------------------------------------- |
| `npm test`                        | vitest unit + integration tests                      |
| `npx playwright install chromium` | once: the e2e suite drives a real browser            |
| `npm run test:e2e`                | Playwright suite against a local build of the Worker |
| `npm run test:e2e:totp`           | the same suite with 2FA required (CI runs this too)  |
| `npm run check`                   | svelte-check                                         |
| `npm run lint`                    | prettier --check + eslint                            |
| `npm run build`                   | production worker + wrapped scheduled handler        |

All of these must pass. CI runs the same list, plus `npm audit --audit-level=high` —
a newly published advisory can fail a build that passes locally, so run it before
pushing a dependency change. `npm run format` fixes formatting.

CI splits that into two jobs. `check` runs the audit, lint, svelte-check, the unit
suite and the build; the browser suites run as a matrix, the main one and the TOTP
one on separate runners at the same time. A push that only touches documentation
skips the browser suites — prose cannot break them — while `check` always runs,
because `tests/platform-setup.test.ts` reads `docs/oauth-apps.md`. Pushing a tag
starts no run at all: the commit it points at was already tested by the branch
push.

The e2e suite keeps to itself: its D1/R2 state lives in `.wrangler/e2e-state`, so
your `npm run dev` data is never touched, and it creates its own account there
(`scripts/seed-local.mjs`, run by the Playwright config) rather than borrowing
yours. If you have no `.dev.vars`, it seeds one from
`tests/e2e/fixtures/dev.vars`; an existing file is used as-is, so put
`SKIP_TOTP=1` in yours to match the path CI takes.

The suite is one serial journey, not independent tests: the first spec signs in
and later ones rely on what it left behind. So `npx playwright test -g "<a later
spec>"` fails on its own — no session, no seeded account — and a `-g` run proves
nothing about that spec. Run the whole file (`npx playwright test
tests/e2e/smoke.e2e.ts`) before believing a failure or a pass.

The e2e server claims port 4173, so stop a running `npm run preview` first (or
change the port in `playwright.config.ts`).

## Code expectations

- **Comments explain why, not what.** The existing files are a good reference: comment the protocol quirk, the retry rule, or the failure mode you are working around — not the syntax.
- **Keep provider code inside `src/lib/server/providers/`** behind the shared `Provider` interface, so a new platform cannot drift from the others.
- **Server code stays on the server.** Everything under `src/lib/server/` and every `+server.ts` runs in a Worker, where the DOM does not exist: lint rejects `window`, `document`, `localStorage` and friends there, so take what you need from `event.locals` instead.
- **No new dependencies without a reason.** The runtime dependency list is deliberately small.
- **Tests for behavior, not for mocks.** Assert what the app does, not that a spy was called.
- **Operator scripts print one line per step.** `scripts/lib/cli.mjs` owns that: colour only on a terminal, and the tool's own output only for `--verbose` or a failure. Add a step, not a wall of output.

## Pull requests

[CONTRIBUTING.md](../CONTRIBUTING.md) has the rules; the checks above are what CI
runs.

An existing build can be tested with `npm run test:e2e:isolated`. Each run creates a fresh local state directory under `.fleet-local` using the bootstrap DDL and a throwaway account. It does not remove prior state, read environment files, or use a personal Wrangler config.

## Interview rehearsal

This fork has an isolated, loopback-only interview workspace. It uses a local SQLite database and media folder under `.fleet-local/rehearsal`; it creates no production credentials, D1 migrations, or provider requests. Its sample Instagram and YouTube destinations are explicitly labeled. Reset demo retains previous databases and media for recovery.

Install the repository's locked dependencies, then supply Mashup's public proof bundle (existing operator output stays where it was rendered; nothing is moved):

```sh
npm ci
npm run demo -- /path/to/mashup/output/public-proof-caption-staging
```

Open http://127.0.0.1:5187/. Calendar shows month/week releases, Review plays real MP4s, and Create offers three starting points. A prepared cut opens instantly with its existing Mashup receipt; it is not represented as a fresh render. Your idea becomes editable post copy. Approval binds the saved video, caption and exact destinations; editing requires a new approval. Rehearse upload records a local receipt, while Schedule release saves real local calendar data. No scheduler runs in this mode.

For fresh local renders, use the Mashup module in `tools/mashup/` with its frozen lockfile. It runs only on the operator's machine and is never bundled into the Worker. FFmpeg, ffprobe and yt-dlp must already be installed; the adapter never installs executables or reads cookies.

```sh
UV_PROJECT_ENVIRONMENT="$PWD/.fleet-local/mashup-runtime" uv sync --project tools/mashup --no-dev --frozen
```

The product lane turns the owner's editable text into three animated scenes and invokes Mashup's approved-edit renderer. It renders native 1080×1920 scenes with measured text wrapping, 4–8-second reading time and an original quiet procedural soundtrack; it uses no synthetic speech or photographic generation. The source-link lane inspects up to three individual public YouTube, Vimeo, Archive.org or Mashup proof links, uses available English captions to choose bounded complete passages up to 24 seconds, and joins them in the owner-approved order. Sentence boundaries are required, long caption gaps are excluded, and ranking favors specific topic words, concentrated relevance and fewer repeated terms. If no complete passage matches the brief within the bound, creation asks for a different brief or source. Captionless sources use the first ten seconds. Source captions are preserved; available captions on new sources are burned in without inventing spoken text. It is an operator-authored edit using text matching and caption boundaries, not a claim of semantic model scoring. Cached renders are keyed by the renderer recipe as well as the approved input, so updated rendering cannot silently return an older video. Source reuse requires the owner's rights confirmation. Login, extractor and rate-limit failures are displayed without cookies, private sources, or bypasses.

YouTube acquisition supports separate video and audio streams, merged to MP4 before Mashup rendering, with source video height capped at 1080 pixels. Fresh acquisition and rendering were verified with two public Creative Commons originals from Conversations with Tyler; this does not guarantee that every public YouTube link is downloadable. Caption punctuation and timing can be inaccurate, and topic-word matching cannot establish semantic relevance, so review the finished video before approval.

The adapter runs `validateOnly` before Mashup render and receipt operations. Completed `fleet.mashup-media-receipt.v1` artifacts are hash-checked before import into Fleet Social. All eight score terms stay in the edit; the operator-authored lane marks them unmeasured. Render progress and outputs remain on disk. Repeating an identical approved plan reuses the hash-verified output instantly and creates a new draft for publishing approval. `npm run demo` uses `tools/mashup` by default; a second argument (`npm run demo -- <proof-folder> <mashup-path>`) points it at another Mashup checkout and is remembered. A finished video enters Review unapproved for publishing.

This adapter is development-only and is removed from the production Worker build. Production continues to require the normal authenticated session. Public publishing needs a deployed HTTPS media route and configured provider OAuth apps; local rehearsal is not evidence of a real provider upload.
