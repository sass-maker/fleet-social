# The validation experiment

mashup exists to answer one question: **does structure-aware sequencing beat
retrieving relevant clips and joining them?** This page is how you run that
comparison and how you read the answer.

> This five-condition design measures the pipeline end to end. It **cannot
> attribute a preference to sequencing**, because its variants are built from
> different clips. For that, see
> [the matched pair](experiment-matched.md), which holds the clips fixed and
> varies only their order.

The comparison is blind and the churn measurement is mechanical, both on
purpose — they live in `src/mashup/experiment.py` rather than in a spreadsheet
someone fills in by hand.

> **Status:** the harness and its `mashup experiment`, `mashup evaluate`, and
> `mashup churn` commands are written and unit-tested. A corrected five-video
> real-archive set was generated fully locally on 2026-07-26; five-viewer
> ratings and evaluation remain outstanding. See
> [`PROJECT_STATUS.md`](../PROJECT_STATUS.md).

## The five conditions

All five are generated from one brief and one target duration, in one run, by
the same planning machinery — see the shared-beam-search decision in
[`docs/decisions.md`](decisions.md#3-one-shared-beam-search-three-weight-profiles).

| Condition | Role | What it does |
|---|---|---|
| `random` | control | Topic-matched clips above a relevance floor, shuffled under a fixed seed |
| `semantic` | the bar to beat | Top-relevance clips in relevance order — "retrieve and concatenate" |
| `chronological` | AI cut | Archive order preserved; selection optimised |
| `escalation` | AI cut | Build to a peak |
| `callback` | AI cut | Plant early, pay off late |

If the planner produces no output for any of the five, the run fails naming the
missing condition rather than quietly comparing four.

## Running it

```bash
uv run mashup experiment \
  --prompt "seven minutes on airline travel" \
  --duration 420 \
  --output study/run-01

# After five viewers complete study/run-01/ratings.csv:
uv run mashup evaluate study/run-01

# Compare a generated timeline with the creator's edited version:
uv run mashup churn output/escalation.json study/run-01/escalation.edited.json
```

The archive must already be ingested, enriched and embedded — see the
[README quickstart](../README.md#quickstart).

### Check the archive can serve the brief

```bash
uv run mashup coverage --prompt "how couples met and got married"
```

`experiment` runs this too and refuses a brief that fails it. Cosine
similarity from an asymmetric encoder never returns anything near zero, so a
topic the archive does not cover still produces five confident-looking
variants. Measured on the dev archive, **nonsense text scores 0.434** against
its ten best matches; `"seven minutes on airline travel"` — three supporting
segments in twenty episodes — scored 0.459, a lift of +0.024.

A five-condition set was generated, rendered and prepared for viewers on that
brief before the floor was measured. Nothing in the pipeline objected.

The check embeds deterministic junk queries, takes what they earn for free, and
asks how far the real brief clears it. `MIN_LIFT` is 0.06, drawn from one
archive and one encoder, so treat a near-miss as "look at the clips" rather
than as a verdict.

### Check the runtimes before recruiting anyone

**All five variants must come out about the same length.** They are ranked
against each other, so an outlier is comparing a different amount of material,
and a visibly shorter file breaks the blinding before playback starts.

`--pool` is the lever. Chronological is the variant that starves: it can only
walk forward through archive order, so a pool too small for the archive leaves
it no valid continuations and it returns early. On the dev archive at the
default pool of 40 it came back at 322.7s against 445.2s for semantic — a 122s
spread. At `--pool 160` the same prompt gives a 40s spread.

Widening is not free and not uniform. On a second prompt the same change
lifted chronological's score from 0.781 to 0.828 while dropping the callback
strategy's callback term from 0.32 to 0.06. So the default stays at 40, the
pool used is recorded in `KEY.json`, and choosing it is part of preparing a
run: plan with `--no-render` first, check the five durations, then render.

## Blind labelling and the withheld key

`run_experiment` shuffles the five conditions under `random.Random(seed)` and
writes them as `A.json` … `E.json`. The mapping never appears in those files.

It goes into a separate `KEY.json` alongside the prompt, target duration, seed,
generation timestamp and each variant's planner score. **`KEY.json` is the file
you withhold from raters.** Hand over the labelled variants (rendered to MP4)
and `ratings.csv`, and nothing else.

`A.json` and `A.srt` sit in the same directory and **the JSON names the
condition** in its `strategy` field. Hand over the `.mp4` files only.

The seed is recorded so the assignment is reproducible: regenerating with the
same seed puts the same conditions behind the same letters.

```
study/run-01/
  A.json  B.json  C.json  D.json  E.json   # blind variants
  KEY.json                                  # withhold from raters
  ratings.csv                               # hand to raters, one row per viewer per variant
```

## The rating sheet

`write_rating_sheet` emits 25 blank rows — 5 viewers × 5 variants — with these
columns:

| Column | Meaning |
|---|---|
| `viewer` | Viewer number, 1–5. Pre-filled. |
| `position` | Watch order for that viewer, 1 first. Pre-filled — see below. |
| `variant` | Blind label A–E. Pre-filled. |
| `overall_rank` | 1 = best of the five, for this viewer. This is the preference signal. |
| `clips_total` | How many clips the viewer counted in this variant. Denominator for the next column. |
| `clips_context_incomplete` | How many clips felt like they needed setup the viewer had not seen. |
| `defects` | Count of obvious repetitions or broken transitions. |
| `would_publish` | yes/no — would a creator ship this? Recorded but not scored by the criteria. |
| `notes` | Free text. |

Analysis ignores any row with a blank `overall_rank`, so a partially completed
sheet still yields a result; a sheet with no completed rows raises.

## Viewing order

Rows are written in the order each viewer should watch, rotated one step per
viewer, so every variant is seen first by exactly one viewer and last by
exactly one:

```
viewer 1   A B C D E
viewer 2   B C D E A
viewer 3   C D E A B
viewer 4   D E A B C
viewer 5   E A B C D
```

Handing all five viewers the same A–E order would confound the variant with
when it was watched. The first is judged on fresh attention and anchors the
scale for everything after it; the last is judged after half an hour of
similar footage. Fixed order pushes both effects onto one condition, and the
ranking could not distinguish that from a real preference.

This balances *position*. It does not balance carryover — which variant
preceded which — because five variants need ten viewers for that. With five
viewers, position is as much as can be balanced, and the residual is worth
recording when the result is read.

**The rater works down the sheet.** Row order is the instruction; do not
sort it.

## Success criteria

`summarise_ratings(outdir)` unblinds the sheet against `KEY.json` and evaluates
three criteria against the **best-performing AI condition** — the one that beat
the semantic baseline for the most viewers.

| Criterion | Threshold | Constant | Computed as |
|---|---|---|---|
| Preference | ≥ 4 of 5 viewers rank it above `semantic` | `PREFERENCE_THRESHOLD = 4` | `beats_semantic[best] >= min(4, viewers)` |
| Context completeness | mean ≥ 0.80 | `CONTEXT_COMPLETE_TARGET = 0.80` | mean of `1 - clips_context_incomplete / clips_total` |
| Defects | mean < 2 per seven-minute cut | `MAX_DEFECTS_PER_SEVEN_MIN = 2` | mean of the `defects` column |

Two details worth knowing before you read the output:

- The threshold is `min(4, viewers)`, so a run with fewer than five completed
  viewers is scored against the number of viewers you actually have. Do not
  read a "pass" from two viewers as a pass of the stated criterion.
- A viewer who ranked the AI cuts but left `semantic` blank contributes nothing
  to the preference count. Incomplete sheets weaken the signal silently.

The summary also returns the per-condition `beats_semantic` counts, the mean
context completeness and mean defect count per condition, so a near-miss can be
read rather than guessed at.

## The kill criterion

**If creators replace more than 30% of the generated timeline, across three
archives, the sequencing is not earning its keep.**

`timeline_churn(original, edited)` measures this mechanically from two EDLs —
the one the planner produced and the one the creator saved out of the editor.

```python
report = timeline_churn(
    load_edl("output/escalation.json"), load_edl("study/run-01/escalation.edited.json")
)
report["churn"]  # 0.0 – 1.0
report["passes_kill_criterion"]  # churn <= MAX_CHURN (0.30)
```

How it is computed:

- Clips are compared by `segment_id`, not by position.
- `removed` = segment ids in the original but not the edit.
- `added` = segment ids in the edit but not the original.
- `churn = (removed + added) / (original_clips + added)`. Additions are in the
  denominator too, so replacing five clips in a twenty-clip timeline reads as
  10/25 = 0.40 rather than 10/20 = 0.50 — a replacement is charged once as a
  removal and once as an addition, against the size of the union.
- `reordered` counts positional disagreement **only among clips that survived
  the edit**, so a removal is not double-charged as a reorder of everything
  after it. It is reported for diagnosis; it does not enter the churn figure.
- Pure reordering therefore yields `churn == 0.0` with a non-zero `reordered`
  count. That is deliberate: if creators keep every clip and only move them,
  the retrieval is right and the *ordering* is what needs work — which is a
  different failure from the one the kill criterion is watching for.

## Reading the result honestly

Three things about this study that its own design does not fix:

- **One archive is not three.** The kill criterion is explicitly cross-archive.
  A good result on Groucho alone proves considerably less than it appears to.
- **`can_open`, `can_end` and `energy` are model judgements** on a domain the
  model has no ground truth for. If the AI cuts lose the blind test,
  disagreement between those labels and human judgement is the prime suspect
  before the objective itself.
- **The weights are hand-set priors.** They are the hypothesis under test, not
  a tuned result. The eight-term breakdown in every EDL is what makes retuning
  tractable after a bad run — see
  [`docs/decisions.md`](decisions.md#5-eight-separate-scoring-terms-all-surfaced).
