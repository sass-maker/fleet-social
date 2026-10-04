# Video quality review - October 4, 2026

Bounded preserve pass on the owner-selected Editorial Planner and existing green product-story template. Codex self-review; the owner has not reviewed these final exports. Historical receipts and media remain intact.

## Actual outputs

- Product: job 28779644-07ec-4448-a35d-2ac800c79fc1, cached review draft 2502cbc2-f077-485f-a027-59658ee27c17. 1080x1920, 13.221333 seconds. Original audio mean -32.8 dB and peak -22.6 dB; no blackdetect events. Three text scenes last 4 / 5.13 / 4.06 seconds with measured 850-pixel safe text width.
- Links: job 95733af1-4e8c-464c-9ff4-1e7b4424e4e6, review draft b474ff48-e798-4d58-963e-50c65ec3e074. 1080x1920, 20.061333 seconds. Audio mean -19.9 dB and peak -1.4 dB. Two complete caption-bounded thoughts from licensed prepared footage; the second starts at 40.05 seconds, replacing the earlier repeated opening quote.
- Both final MP4 hashes match canonical Mashup media receipts. Both play in Chrome and remain awaiting approval. A repeat product request returned a labeled cached result immediately and a separate unapproved draft.

## Defects found and fixed

1. Source-size text was soft after scaling and a long literal word could spill beyond the safe area. Product sources now render natively at 1080x1920; actual font measurements wrap long words and reduce font size only when necessary. The middle-scene frame retains readable four-line text inside the margins.
2. Fixed-duration cuts stopped mid-sentence and repeated similar material. Caption-window scoring now favors relevant terms, sentence boundaries and lower overlap with preceding excerpts. This is a deterministic heuristic, not semantic model understanding.
3. Full-frame fades caused dark transitions and static scene duration ignored reading time. The background stays stable with a brief text reveal and reading-based 4-8 second timing; full-file blackdetect found no black flashes.
4. Product output was silent. A newly synthesized quiet instrumental bed fades in/out; the soundtrack is recorded as an original source in the receipt. No synthetic speech or borrowed song is used.
5. The initial quality render added another heading and watermark over prepared videos that already included them. Final all-preformatted-source plans suppress that extra layer. The final source poster and second-source frame show one title/caption treatment.
6. Older outputs could satisfy an unchanged input after renderer changes. Cache identity now includes both renderer source files. The old recipe and files remain; this pass rendered fresh outputs for the new recipe.

## Rendered app review

Nine fresh Create/Product review/Links review captures and zero-overflow checks at 390, 768 and 1440 pixels are in responsive-checks.json. The phone player and details stack; 768px keeps a focused player beside details; desktop retains paper, green actions and amber approval status. The heading, actual video, caption, source credits and approval action have a clear reading order. Georgia headings and native sans controls preserve the established direction. No layout or navigation redesign was made.

The captures preserve the initial paused native video-controls state, including Chromium loading glyphs. Separate real-browser playback verified readyState 4 and full playback. The exported frame captures provide direct evidence of the video composition; native controls are not burned into the output.

## Advisory scanner

Pinned slop-detect 0.8.0 / cdd58e1 scored Create 11 and both review surfaces 7 at iteration and final. cream_default_bg is the intentional paper surface; icon_card_grid matches the functional three-step workflow rail. Neither warrants changing the selected system. Iteration retains the initial quality drafts for comparison and final targets the corrected files. This page scanner does not assess video/audio quality.

## Deductions and practical limits

Craft self-review 35/40: hierarchy 7/8 (the review title remains generic); typography 7/8 (native source title styles vary); composition 7/8 (product story remains sparse text rather than photographic footage); identity 7/8 (different licensed source treatments remain); interaction 4/4 (real playback and labeled cached/unapproved state verified); responsive 3/4 (mobile review is a long stacked page). Audit 18/20: purpose 4/4, accessibility 5/6 (no new assistive-technology user review), behavior 4/4, responsive 3/4, performance 2/2 for this bounded local pass.

Captionless sources use the disclosed first-ten-second fallback. External raw YouTube/Vimeo acquisition was not newly qualified live in this pass; public captions can be unavailable. The examples use verified licensed prepared footage. Real social OAuth/publishing remains unqualified; sample destinations do not represent linked accounts. No new dependencies, production deployment or synthetic voice.

## Checks

Seven Python regression cases pass; 121 unit files / 1027 tests pass; Svelte check has zero errors and warnings; lint and production build pass. JS checks are recorded in .fleet-local/video-quality/verification.json. The last Python-only title-layer correction is covered by both fresh canonical renders and file/audio/frame checks. Production Worker output was scanned and has no rehearsal auth/renderer markers. The prior full rehearsal browser suite passed 54 tests before this quality pass and was not represented as rerun here.
