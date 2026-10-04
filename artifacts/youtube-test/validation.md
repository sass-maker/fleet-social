# Fresh YouTube-link qualification - October 4, 2026

Two raw public YouTube links completed the app workflow: metadata inspection, caption acquisition, bounded source acquisition, canonical Mashup approved-edit validation/render, timed subtitle burning, verified media receipt, local draft import, and full Chrome playback. No prepared media was substituted for these source downloads.

## Reproduced failure and fix

The first run failed on source 1 with Requested format is not available. Its format selector required a combined video/audio file. Current source formats exposed separate streams. The scoped fix uses bv*[height<=1080]+ba/b[height<=1080] and MP4 merging, keeping time ranges, file-size checks and the existing 1080-pixel source bound. This follows [yt-dlp format selection](https://github.com/yt-dlp/yt-dlp#format-selection). The failed job and its error remain at .fleet-local/rehearsal/jobs/01d3414e-f2fa-4f98-b50d-f988d6b3a37f. A new spec was skipped under the spec-driven skill exemption for scoped regressions; this remains part of Fleet Social issue 10.

## Media evidence

- Backend: yt-dlp 2026.08.19. Mixed public video and English subtitle acquisition; two inspected, two downloaded, zero failed after the fix. No download archive was used.
- Sources: Joel Mokyr on Clans, Corporations, and a Culture of Growth (https://www.youtube.com/watch?v=y4Orjmn21VI), Creative Commons Attribution license (reuse allowed); Chase Koch on Principles, Music, and Overcoming Entropy | Conversations with Tyler (https://www.youtube.com/watch?v=jMR08XbWOgw), Creative Commons Attribution license (reuse allowed). Both licenses were inspected live before acquisition.
- Fresh job: 6b71a35b-2d38-4e9c-9c27-3f4abd4390d9. Original unapproved draft: 595efa93-9da0-4a11-9638-a2808019c542. Cached repeat created a separate unapproved draft: aaa41955-12c8-4524-8852-33d86ebb2b6b. The UI labeled reuse; no new source acquisition occurred for the repeat.
- Output: 1080x1920, 23.96 seconds, 3950564 bytes. Fresh preparation/render took approximately 82 seconds.
- Two bounded source files: 3489347 / 2086134 bytes. Time ranges are recorded in result.json and acquisition-0/1.json. Full source programs were not downloaded.
- FFmpeg post-processing ran. Final MP4 and caption hashes match their canonical Mashup receipt. Mean/peak audio: -19.2/-1.5 dB, audible without clipping.
- Chrome played through all 23.96 seconds and ended at readyState 4. Both original and cached drafts remain awaiting approval. The saved review captures fit 390/1440 pixels with zero document overflow; initial native player controls are captured before user playback.

## Quality judgment and boundaries

Acquisition/render/playback passes for these two sources. Automatic editorial quality still needs review. The first passage stops after build roads and the second references Rafa and a hero journey; caption-window keyword scoring can prefer a fragment or a loosely related passage. The Create copy and existing development documentation now disclose that limitation instead of promising complete excerpts. No semantic-model scoring is claimed.

The original interview footage remains fitted within a vertical frame; it is not an automatically tracked speaker crop. Source headings may abbreviate long titles, while full source titles/URLs stay in credits. Video frames are saved as frame-0/1.jpg. This bounded downloader correction and copy clarification preserve the selected Editorial Planner; no visual redesign was performed.

No cookies, account login, proxies, private sources or DRM bypass were used. Downloading does not authorize publication. These checks do not guarantee every public YouTube URL is downloadable. Real YouTube/Instagram OAuth and publishing remain unqualified; rehearsal destinations are samples. No commit, push, deployment or production migration.

## Repository checks

Seven Python tests and 121 unit files / 1027 tests pass. Svelte check reports zero errors/warnings; lint and production build pass. The production scan examined 52 JavaScript files and found zero rehearsal auth/renderer markers. Commands and timings are retained in .fleet-local/youtube-test/verification.json.

Both default demo examples were rendered again under the corrected recipe so their next identical requests can reuse cached verified bytes. Older exports/receipts were retained. The dirty primary checkout remains unchanged. The running local demo is http://127.0.0.1:5187/ and the writer stays unpublished.
