# The matched pair: testing sequencing on its own

The [five-condition study](experiment.md) measures the pipeline end to end.
It cannot tell you whether *sequencing* helped, because its five variants are
built from different clips. Measured on the dev archive, the chronological cut
shared **0–5%** of its material with the other four conditions:

| Jaccard on clip sets | chrono | semantic | random | callback | escalation |
|---|---|---|---|---|---|
| chronological | 1.00 | 0.05 | 0.00 | 0.05 | 0.05 |
| semantic | 0.05 | 1.00 | 0.00 | 0.31 | 0.31 |
| random | 0.00 | 0.00 | 1.00 | 0.11 | 0.05 |
| callback | 0.05 | 0.31 | 0.11 | 1.00 | 0.54 |
| escalation | 0.05 | 0.31 | 0.05 | 0.54 | 1.00 |

A viewer preferring the chronological cut there could be responding entirely
to it having drawn better clips. The project's claim is about ordering, so the
experiment has to hold the clips fixed.

```bash
uv run mashup experiment --matched \
  --prompt "how couples met and got married" \
  --duration 420 --output study/matched-01
```

Two arms from one clip set:

| Arm | What it is |
|---|---|
| `planned` | the planner's own order |
| `shuffled` | the same clips in an arbitrary order |

Because the clips are identical, both arms render to the same length to the
millisecond — 399.002s each on the reference run — so runtime cannot leak the
blinding the way it can in the five-condition design.

## Why the comparator is the median shuffle, not the first one

Only **18.6%** of this archive's segments are marked `can_end`, so most orders
take the 6% unfinished-ending penalty and a few do not. That penalty is a large
discrete step next to the differences ordering produces.

The first configuration tried drew a shuffle that landed the single `can_end`
clip last — an 8.1% event — and it outscored the planner's order overall
*despite being worse on every order-sensitive term*. Handing that to viewers
would have been handing them a comparator that is not arbitrary at all.

So the pair is chosen by drawing `MATCHED_SHUFFLES` orders and taking the one
whose score is the **median**. The comparator stays random but becomes typical.

This does mean the objective picks which random order is representative. If the
objective is wrong about endings, so is that choice — which is why `KEY.json`
records both arms' scores and the full shuffle distribution rather than only
the winner.

## Check the planner is ahead before recruiting anyone

```bash
uv run mashup order-test --prompt "how couples met and got married"
uv run mashup order-test --prompt "..." --sweep --pools 40,80,160
uv run mashup order-test --study study/matched-01
```

This is the mechanical proxy for the whole study, and it costs seconds rather
than six people's evening. It shuffles a clip set many times and reports where
the planner's own order lands. It exits non-zero when the planner is not
confidently ahead.

`--study` audits a set that already exists: shared material between variants,
each variant's percentile, and which terms fail to discriminate. It names the
conditions, so it is an operator tool — do not run it on a rater's screen.

`KEY.json` carries the same numbers in an `order_stats` block, and the CLI
prints them at generation time:

```
planner order scores 0.6816, ahead of 100% of 200 arbitrary orders
comparator (median shuffle) scores 0.5788
```

**If `planned_percentile` is near 50, stop.** The planner's own objective is
saying it did not order those clips well, and a null result from viewers would
be confirming that rather than testing the thesis. The CLI warns below 75.

This number moves a lot with the configuration, so pick the arm before you
recruit. On `"how couples met and got married"` at 420s:

| pool | strategy | planned | median shuffle | percentile |
|---|---|---|---|---|
| 40 | **escalation** | 0.6816 | 0.5743 | **100.0%** |
| 40 | callback | 0.5731 | 0.5057 | 97.2% |
| 40 | chronological | 0.6459 | 0.6246 | 95.0% |
| 60 | escalation | 0.6110 | 0.5780 | 75.8% |
| 60 | chronological | 0.6753 | 0.6351 | 100.0% |
| 120 | escalation | 0.6651 | 0.5559 | 100.0% |

Percentile ties are broken on the gap, so `--sweep` names escalation at pool
120 (+0.1092) over pool 40 (+0.1073). That difference is noise; the shipped set
uses pool 40 because it is the default and the two are indistinguishable.

Choosing on the objective before any human has rated anything is
pre-registration, not p-hacking: the objective is the stated hypothesis, and
the outcome being selected on has not been observed yet. Record the choice.

### Two readings that look like signal and are not

**A baseline's percentile is the ending penalty, not ordering.** The `semantic`
and `random` profiles are relevance plus duration — 100% order-invariant. A
shuffle cannot change any of their terms; it can only change whether the
sequence happens to end on a `can_end` clip. `semantic` scoring at the 77.5th
percentile in an audit is that 6% penalty and nothing else. `order-test` labels
these rows.

**Identical clip sets make the order-invariant terms identical.** Auditing a
matched pair reports `relevance`, `non_repetition`, `duration_fit` and
`source_diversity` at spread 0.000. That is the design working, not four dead
terms, and the audit says so.

## Reading the result

Six viewers, alternating which arm plays first — three each way, which is what
balances the order effect across an even number of viewers.

```bash
uv run mashup evaluate study/matched-01
```

`summarise_matched` runs an exact two-sided sign test on the viewers who
expressed a preference. Viewers who ranked the two arms equal are excluded from
the denominator and reported separately.

**Six viewers can only reach p < 0.05 at unanimity.** 6–0 is p = 0.031; 5–1 is
p = 0.219. The command reports the count and the exact p-value and calls only
the former significant. A 5–1 split is worth acting on as evidence and is not a
result, and the code will not round it up into one.

**That makes one pair badly underpowered.** With a rejection region of only
{6–0, 0–6}, power is `p⁶ + (1−p)⁶`:

| if the planner's order is truly preferred… | chance of detecting it |
|---|---|
| 70% of the time | 12% |
| 80% of the time | 26% |
| 90% of the time | 53% |

So the likely outcome of a single pair is an ambiguous null even when the
planner is good. Reaching a usable answer needs roughly **40–50 judgments**,
and the cheap route is more pairs per rater across several prompts rather than
more raters. Judgments from one person are correlated, so read them per rater
rather than pooling them into one sign test.

The test is two-sided on purpose: the shuffled arm winning 6–0 is a real
finding about the planner, not a null one.

## What this design still does not fix

- **One archive, one prompt.** The kill criterion in
  [experiment.md](experiment.md#the-kill-criterion) is explicitly cross-archive.
- **Selection is untested.** This isolates ordering by holding selection
  constant, which means it says nothing about whether the planner picks better
  clips than top-relevance retrieval. That is what the five-condition study
  measures, confounded.
- **Carryover.** With two arms and six viewers, position is balanced and order
  pairs are trivially balanced, so this design is cleaner than the
  five-condition one on that count.
