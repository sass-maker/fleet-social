# API

The browser UI uses the `cog_session` cookie after TOTP. Scripts, Shortcuts and
cron use a personal API key instead — no login, no cookies. Manage it in
**Settings → API access** (generate, rotate, revoke); the raw key is shown once
and only its hash is stored. Worked examples for the common calls live in-app at
`/api`.

## Authentication

Export your instance URL and a key from **Settings → API access**:

```sh
export APP_URL=https://cogsend.<account>.workers.dev
export COGSEND_API_KEY=cog_...

curl -s "$APP_URL/api/connections" -H "Authorization: Bearer $COGSEND_API_KEY"
```

`X-API-Key` works as an alternative header; never put the key in the URL. The key
acts as you on drafts, variants, media, publish, schedule, queue, settings, and
reads — but it can never connect, re-verify or disconnect accounts, or create,
rotate, or revoke keys (those stay in the browser session). The global
`API_TOKEN` Worker secret still works as a bearer for backwards compatibility, on
exactly the same routes as a personal key — it cannot reach the session-only ones
either — but prefer the personal key for scripts: it is revocable without
touching the scheduler.

## Examples

Create a draft, then publish it to one account:

```sh
curl -s -X POST "$APP_URL/api/drafts" \
  -H "Authorization: Bearer $COGSEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"Hello","baseBody":"from a script"}'

curl -s -X POST "$APP_URL/api/drafts/DRAFT_ID/publish" \
  -H "Authorization: Bearer $COGSEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"connectionIds":["CONN_ID"]}'
```

## Limits

- A body is capped at 100,000 characters, and a variant may carry at most 100 explicit `threadSegments`.
- Each platform's own text and media limits are checked again at publish, so what the API accepts is not necessarily what a platform will take.

## Publishing behaviour

- Publishing the same draft and account twice reuses the row. Already-published accounts come back `skipped: true`.
- A publish that is still running on that account answers **409** with `inFlight` (the connection ids) — wait, then try again.
- A retried segment carries the same platform-side id as its first attempt, so a thread that failed half-way does not double-post what already went out (Mastodon remembers the id for an hour, Bluesky refuses to overwrite the record).
- Sending several connection ids in one request publishes them in order. The first always runs; each further one runs only if it fits in what is left of the request's Cloudflare call budget (50 on Workers Free, see `SUBREQUEST_LIMIT` in [Configuration](configuration.md#secrets)). The ones that don't fit come back with `status: "pending"` and `deferred: true`. They are already due and go out on the next scheduler tick, so don't send them again. For the fastest results, send one connection id per request. If a request still runs out, it answers `200` with `stopped: true`, `stoppedError`, and the results it did get — the accounts after the last entry were not completed and are still due (a target the failure interrupted is left retryable, never `publishing`), so send those ids again. A `500` means nothing was recorded; check the draft before retrying.
- Do not call `/api/targets/:id/retry` unless the row is `failed` (or a stuck `publishing` older than 15 minutes).
- Schedule returns **409** if that account is already published or still publishing. Check `error`, `alreadyPublished`, and `inFlight` instead of treating HTTP 200 as "it was scheduled".

# Fleet Social draft intake

Fleet product feeds should use a key with only the `intake` scope, generated in Settings. It can call `POST /api/drafts` and cannot approve, schedule, publish, or reconcile outcomes. The instance has one active API key, so generating an intake key replaces any existing API key; browser sessions remain separate.

Send a canonical active `projectId` and a stable `sourceRef` for idempotency:

```json
{
	"projectId": "codevetter",
	"sourceRef": "release:1.14.3",
	"baseBody": "Draft announcement for owner review"
}
```

The first request creates a draft; a repeated `(projectId, sourceRef)` returns the existing draft without changing its content. The owner selects connected destinations and approves the saved revision in Compose. Any copy, media, platform variant, or destination change requires renewed approval. If a provider response is uncertain, Posts asks the owner to inspect that account and record the live post URL or confirm no post exists before a manual retry.

## Generated media

Any generator hands its finished video or image to Fleet Social the same way: a local clipping agent such as Mashup, a cloud render job, or a generation API called on a schedule. Create or find the draft as above, then attach the file to it with `POST /api/drafts/:id/media` as multipart form data with a `file` field and a `receipt` field holding this JSON:

```json
{
	"schema": "fleet.media-receipt.v1",
	"generator": { "kind": "cloud", "name": "clip-agent", "version": "2.1.0", "runId": "run-8812" },
	"output": { "sha256": "<hex digest of the file>", "bytes": 4819230, "mime": "video/mp4" },
	"sources": [
		{
			"title": "Founder interview",
			"creator": "Example Show",
			"sourceUrl": "https://example.com/episode-4",
			"license": "CC BY 4.0",
			"licenseUrl": "https://creativecommons.org/licenses/by/4.0/"
		}
	]
}
```

- `generator.kind` is `local`, `cloud` or `api`. Mashup's own `fleet.mashup-media-receipt.v1` export is also accepted as it is.
- The server hashes the upload and refuses it with **400** unless the size, type and SHA-256 match the receipt. Posting the same file to the same draft again returns **200** with `existing: true` and stores nothing new, so a generator can retry after a dropped connection.
- An intake key needs a receipt, and it can only add media to a draft that intake created and that the owner has not yet approved or scheduled. After that it gets **409**, and changes are made by the owner.
- Review shows the generator, the matched digest and the credited sources. Approval still belongs to the owner's session.
- Uploads are limited to 95 MB per file.

The `media:push` script does both calls for a file on disk. It sends the instance's own `Origin`, which SvelteKit requires on every multipart POST. Any other client must send it too. Set `FLEET_SOCIAL_URL` and `FLEET_SOCIAL_API_KEY`, plus `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` when the instance is behind [Cloudflare Access](access.md):

```sh
npm run media:push -- --project codevetter --source-ref mashup:edit-42 --file out/video.mp4 --receipt out/media-receipt.json
npm run media:push -- --project codevetter --source-ref clip-agent:run-8812 --file clip.mp4 --generator clip-agent --kind local --caption "Draft caption"
```

Without `--receipt`, the script writes a receipt for the named generator from the file itself.

## Upstream API reference
