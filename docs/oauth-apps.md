# OAuth apps and platform limits

Mastodon and Bluesky connect with what you already have. YouTube, LinkedIn, Threads and X
need an app registered at the provider first, because they issue the client id
and secret the Worker uses.

## OAuth app setup

Each one is the same three steps: create the app, add the redirect URI, set the
Worker secrets.

| Platform | Where                                                                 | Redirect URI                                  | Worker secrets                                 |
| -------- | --------------------------------------------------------------------- | --------------------------------------------- | ---------------------------------------------- |
| LinkedIn | [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) | `{APP_URL}/api/connections/linkedin/callback` | `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` |
| Threads  | [Meta for Developers](https://developers.facebook.com/apps/)          | `{APP_URL}/api/connections/threads/callback`  | `THREADS_APP_ID`, `THREADS_APP_SECRET`         |
| X        | [X Developer Portal](https://developer.x.com/en/portal/dashboard)     | `{APP_URL}/api/connections/x/callback`        | `X_CLIENT_ID`, `X_CLIENT_SECRET`               |
| YouTube  | [Google Cloud Console](https://console.cloud.google.com/)             | `{APP_URL}/api/connections/youtube/callback`  | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`   |

The last step — putting those secrets on the Worker — works either way:

- **From a checkout.** Put the values in `.dev.vars` and run the platform's
  command below. The example file ships every optional key commented out:
  uncomment the line you fill in, or add it if it is not there. `secrets:put`
  reads that file, then reads the Worker's own secret list back to confirm what
  landed, and exits non-zero naming anything it did not find.
- **Without a checkout.** Add each one in the Cloudflare dashboard — Workers &
  Pages → your Worker → Settings → Variables and Secrets → Add → **Secret** —
  using the names in the table above. The dashboard applies the change when you
  press **Deploy** in the same flow.

From the CLI there is no redeploy step at all: the secrets are live as soon as
the command finishes. Either way, reload the accounts page when you are done.

### YouTube

1. In a dedicated Google Cloud project, enable **YouTube Data API v3** and configure a Google Auth Platform consent screen. Add the channel owner's Google account as a test user while the app is in Testing.
2. Create a **Web application** OAuth client with the redirect URI in the table above. This client is separate from any Google login client used by Cloudflare Access.
3. Add only `https://www.googleapis.com/auth/youtube.upload` and `https://www.googleapis.com/auth/youtube.readonly` in Google Auth Platform → Data Access. Upload creates the video; read access identifies the channel and reconciles interrupted uploads.
4. Set `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` as Worker secrets, reload Accounts, and connect the owner's channel.

The composer accepts one MP4 up to 95 MB, a title of 1–100 characters, and a description up to 5,000 characters. Every upload is **Private** while public upload eligibility for this Google API project remains unverified. The scheduler uses YouTube's resumable upload protocol, preserves progress across retries, and records the returned video ID and visibility in Posts. Google OAuth apps left in Testing can issue refresh tokens that expire after seven days for these scopes; plan for reconnection or complete Google's production verification before relying on a long-running schedule. See [Google's OAuth documentation](https://developers.google.com/identity/protocols/oauth2#expiration) and [YouTube's upload reference](https://developers.google.com/youtube/v3/docs/videos/insert).

### LinkedIn

1. Create an app in the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps).
   It has to be attached to a LinkedIn Page.
2. On **Products**, request **Share on LinkedIn** (grants `w_member_social`,
   which publishes) and **Sign In with LinkedIn using OpenID Connect** (grants
   `openid profile email`, which fills in the account name and avatar).
3. On **Auth**, add the redirect URI under **Authorized redirect URLs for your
   app**, and copy the client id and secret from **Application credentials**.
4. Set the secrets:

   ```sh
   npm run secrets:put LINKEDIN_CLIENT_ID LINKEDIN_CLIENT_SECRET
   ```

LinkedIn issues refresh tokens only to approved Marketing Developer Platform
partners, so a self-created app gets a 60-day access token and nothing to renew
it with. Posts keep going out until that token expires; after that the account
shows as expired, and **Reconnect** on the Accounts page starts a new 60 days.

### Threads

1. Create an app in the [Meta for Developers](https://developers.facebook.com/apps/)
   dashboard with the **Access the Threads API** use case.
2. Add the redirect URI under that use case's **Redirect Callback URLs**, and
   copy the app id and secret.
3. While the app is still in development, add the Threads account you want to
   connect as a **Threads tester** and accept the invite from the Threads app.
4. Set the secrets:

   ```sh
   npm run secrets:put THREADS_APP_ID THREADS_APP_SECRET
   ```

### X

1. Create a Project and an App in the [X Developer Portal](https://developer.x.com/en/portal/dashboard).
2. In the app's **User authentication settings**, turn on OAuth 2.0 with the app
   type **Web App**, and add the redirect URI as the **Callback URI / Redirect
   URL**.
3. Copy the client id, plus the client secret if the app is a confidential
   client (CogSend also completes the exchange with PKCE alone).
4. Set the secrets:

   ```sh
   npm run secrets:put X_CLIENT_ID X_CLIENT_SECRET
   ```

Posting on X uses pay-per-use API credits — fund a small balance in the console
first.

Moving your instance to another hostname means registering these redirect URIs
again; see [Domains and URLs](domains.md).

Without these, those three platforms are listed in the accounts dialog with a
**Needs setup** badge. Picking one shows that platform's own steps — the redirect
URI to register and the Worker secrets to set — instead of a connect attempt that
cannot succeed. Mastodon and Bluesky keep working either way.

## Platforms

| Platform | Auth                                                                     | Text                        | Images                                 | Threads                      |
| -------- | ------------------------------------------------------------------------ | --------------------------- | -------------------------------------- | ---------------------------- |
| Mastodon | OAuth (per instance)                                                     | instance max (default 500)  | 4, 16MB                                | yes                          |
| Bluesky  | handle + app password                                                    | 300                         | 4, 1MB                                 | yes                          |
| LinkedIn | OAuth (`openid profile email w_member_social`)                           | 3000                        | 4, 8MB (no WebP)                       | no — flattened into one post |
| Threads  | OAuth (`threads_basic threads_content_publish` `threads_manage_replies`) | 500, max 5 links            | 4 uploadable, 10 allowed, 8MB JPEG/PNG | yes                          |
| X        | OAuth 2.0 + PKCE                                                         | 280, max 1 cashtag          | 4, 5MB (15MB GIF)                      | yes                          |
| YouTube  | OAuth (`youtube.upload` and `youtube.readonly`)                          | title 100; description 5000 | one MP4, 95 MB; private                | no                           |

LinkedIn rejects WebP at publish time — upload JPEG, PNG or GIF.

Threads images are served to Meta through short-lived signed URLs (2h expiry,
never linked publicly). Meta's crawler intermittently fails to fetch a URL that
works moments later (subcode 2207052), so media containers retry with a freshly
signed URL and the failure stays retryable. A custom public media origin can be
configured with `MEDIA_PUBLIC_BASE_URL` (for example an R2 custom domain behind
Cloudflare's cache) to skip the Worker hop entirely; that path is a trade-off —
those URLs are not signed and never expire, protected only by the randomness in
the object key, so keep the origin unlisted and treat a leaked URL as permanent.
Remove the variable to go back to signed URLs.

LinkedIn video is off unless you set `ENABLE_VIDEO_UPLOAD=1` as a Worker secret or var. LinkedIn takes one MP4 per post, with no images mixed in. Its upload path is wired but has not been verified end to end against LinkedIn's live API, which is why it ships disabled. YouTube video becomes available when its OAuth client secrets are configured; it does not use this LinkedIn flag.
