# Retrospective, 2026-07-26: the instrument and the question

Three sessions of work on the validation experiment produced a great deal of
correct engineering and **zero human judgments**. This page records why, and
one claim that needs walking back.

## The claim I overstated

I reported that the planner's order beats 99.4–100% of shuffles of its own
clips, and called it "the encouraging half."

It is weaker than that. The measurement compares the optimiser against **its
own objective**. Beam search optimises that objective, so of course it wins.
The result says the search is not broken — a real thing to know, and it would
have been worth finding out if it were false — but it is **not** evidence that
the objective matches human taste. It is circular with respect to the thesis.

There is still no evidence for or against the central claim. Nobody has
watched anything.

## The failure mode

Every session found a genuine defect and fixed it:

| session | found | fixed |
|---|---|---|
| 1 | fixed cosine thresholds, boilerplate callbacks, short-sequence bias | yes |
| 2 | no counterbalancing, 122s duration spread leaking the blinding | yes |
| 3 | prompt with no material, conditions sharing no clips, `rescore` skipping the ending penalty | yes |

Each fix was necessary. None produced data. Apparatus-building has no natural
stopping point — there is always another defect, because there always is one —
so "the harness isn't ready yet" can absorb unlimited effort while the
hypothesis goes untested. That is what happened here, and the agent doing the
work (me) drove it.

## The design cannot detect what it is looking for

Six viewers, one matched pair, binary preference. The sign test's rejection
region at p < 0.05 is only 6–0 or 0–6, so:

| if the planner's order is truly preferred… | chance this study detects it |
|---|---|
| 70% of the time | **12%** |
| 80% of the time | **26%** |
| 90% of the time | **53%** |

Power is `p⁶ + (1−p)⁶`. Even a dominant planner is missed half the time. The
overwhelmingly likely outcome is an ambiguous null, whose natural response is
another round of instrument-fixing.

**Roughly 40–50 judgments** are needed for a 70/30 effect. The cheap way to get
them is not more people — it is more pairs per person, across several prompts.
Judgments from one rater are correlated and want a per-rater read rather than
one pooled sign test, but 20 pairs from one person still carries far more
information than 6 from six.

## The corpus may not support the question

Twenty episodes of one quiz show: ~40-second exchanges, different guests each
time, no narrative through-line to construct. Whether "escalation" is even
*perceptible* across unrelated interviews is an open question independent of
the software. If ordering effects on this material sit below the threshold of
human noticing, no planner wins and the null says nothing about the thesis.

This is testable in forty minutes by one person watching matched pairs, and it
should have been the first thing done rather than the last.

## The goal did not move; the emphasis did

Described afresh, the goal is: **give it a set of source videos and get back
one coherent, delightful video.** That is the same goal the repo was built
for. Whether the sources are one creator's back catalogue or a folder of
unrelated downloads is provenance, not function — ingestion reads a directory
of media files and does not care.

I initially wrote this up as a pivot. It is not one, and treating it as one
would have licensed a lot of unnecessary rework.

What the restatement does sharpen:

- **"A delight" is the target, not "statistically significant."** A
  real-but-small ordering effect is fine for a product and fatal for a
  thesis-gated project. "A validation experiment before it is a product" is
  what blocks shipping, and it is a framing choice rather than a fact.
- **Selection matters at least as much as ordering.** The thesis casts
  retrieval as the strawman to beat. If the output has to be a delight, what
  gets extracted is at least half the work, and no current experiment isolates
  it.
- **Heterogeneous sources are untested.** Every run so far used one show: one
  host, one format, one audio chain. `required_context` and `can_open` are
  judged per clip with no notion of what a viewer arriving from a *different
  video* knows. This is a coverage gap, not a redirection.

What it does **not** change, checked rather than assumed:

- Ingestion is a directory of media files (`.mp4/.mkv/.mov/.mp3/.m4a/.wav`),
  so files fetched by any means already work. There is no YouTube-shaped hole.
- Rendering already applies per-clip EBU R128 `loudnorm` and letterboxes every
  clip to a common size and fps, so the obvious cross-source jarring — loudness
  jumps, aspect changes — is handled.
- Missing subtitles are transcribed locally.

One real gap found: `_target_format` takes resolution and fps from the **first
video source**. Across heterogeneous inputs that silently pins the whole render
to whatever the first file happens to be, so a 360p clip first in the archive
downscales everything.

## What this is worth

The engineering is sound and the diagnostics now in the repo are genuinely
reusable. But the project has been optimising a proxy for three sessions
without once checking the proxy against the thing it proxies. The next action
has to produce a human judgment, not a better instrument.
