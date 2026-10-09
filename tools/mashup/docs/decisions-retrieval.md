# Decision log — retrieval and scoring

Continues [`decisions.md`](decisions.md). Entries 12–14 all came out of one
change — making the embedding model swappable, which exposed how much of the
scoring layer had been silently fitted to a single model's behaviour. Entry 15
finishes the job by moving the last networked stage in-process.

---

## 12. Embeddings run locally by default

**Context.** Embeddings are re-run every time retrieval is tuned, and the first
gateway run needed four passes to drain 727 segments through rate limits and
provider fallbacks.

**Decision.** A local HuggingFace encoder is the default backend
(`BAAI/bge-base-en-v1.5`, 768d, CLS-pooled, L2-normalised). The gateway remains
available behind `MASHUP_EMBED_BACKEND=gateway`. Both satisfy one `Embedder`
protocol, so nothing downstream knows which is in play.

**Why.** The same 727 segments now embed in about nine seconds with no network
and no key, which turns re-embedding from a chore into a non-event. It also
removes the failure mode that did the most damage: the gateway silently falls
back between providers mid-run, and a run that mixes two vector spaces produces
confident, meaningless rankings. bge-base was chosen over a smaller encoder
because it was already in the local HuggingFace cache and is the same family as
the `bge-large-en-v1.5` the gateway run was pinned to, so results stay broadly
comparable across backends.

**Consequences.**

- **Asymmetric embedding.** BGE-family models are trained with a prefix on the
  query side only. The brief and its beats get it; transcript segments do not.
  A `required_context` string ("the audience knows he is a plumber") is a
  statement compared against other transcript, so it takes the document side.
- **Vectors record their model.** Two 384-dimension models mix without any
  dimension check noticing. `segments.embedding_model` is the only thing that
  can catch it, so `mashup embed` re-embeds automatically on a model change and
  `mashup status` reports what is stored.

**Trade-off accepted.** torch and transformers are a large optional dependency
for a tool whose other requirements are small. They live in a `local` extra,
present in the dev group so `uv sync` produces a working default, absent from a
gateway-only install.

---

## 13. Similarity thresholds are calibrated, not hard-coded

**Context.** The scoring terms carried fixed cosine cuts — 0.82 for redundancy,
0.55 for context coverage, a (0.30, 0.72) flow band. Swapping the embedding
model exposed them as accidental.

**Decision.** Each run measures the cuts from percentiles of the candidate
pool's own pairwise similarity distribution: p99 for redundancy, p25–p90 for
the flow band, p25 of the prerequisite-match distribution for context coverage.
The fixed values survive only as fallbacks below twelve embedded segments,
where percentiles are noise. The chosen cuts are written into the EDL so the
editor rescores against the same thresholds the build used.

**Why.** A fixed cosine threshold is a claim about one model's similarity
scale, not about comedy. Under bge-base this archive puts 99.9% of segment
pairs below 0.841, so the 0.82 redundancy cut fired on almost nothing:
`non_repetition` returned 1.00 for every candidate sequence — a term that had
stopped measuring anything while still looking healthy in the breakdown. The
same shift pushed nearly every adjacent pair inside the fixed flow band and
flattened `progression` too. Calibration is what makes the encoder swappable at
all.

**Trade-off accepted.** Scores are no longer comparable across runs with
different candidate pools, which is why the calibration is recorded rather than
recomputed. A tied block at the top of the distribution would put the
redundancy cut at the ceiling and switch the term off, so that case falls back
to the midpoint between median and maximum.

---

## 14. The callback strategy gets its own candidate pool

**Context.** The callback strategy scored 0.00 on callback while the random
control scored 0.35 — losing at its own objective to noise.

**Decision.** Three changes. Callbacks are counted only across a gap, across
two *different* recordings, and only on entities appearing in at most 5% of the
archive. And the callback strategy plans over the MMR pool plus segments that
reuse an entity already in it.

**Why.** Each of the three sources of the failure was invisible on its own.

- MMR exists to strip near-duplicate material, and two clips about the same
  running gag are near-duplicates in embedding space — so the retrieval stage
  was removing every plant-and-payoff pair before the planner saw the pool.
  Measured: zero cross-gap entity repeats in the MMR pool, several in the
  undiversified one.
- This corpus names the host in 96 segments and the sponsor in 48. Without a
  frequency filter almost any two clips read as a callback.
- A name recurring inside one episode is the original conversation continuing.
  Counting it credited the planner for something the source already did, which
  is precisely how the random control was outscoring the strategy.

**Result.** Callback is now the only condition scoring above zero on callback
(0.15, against 0.00 for both baselines).

**Trade-off accepted.** One strategy planning over a different pool is a
confound in the blind comparison, and it must be reported alongside any result.
The alternative — a strategy structurally unable to do the thing it is named
for — is worse.

---

## 15. Enrichment runs on a local mlx model

**Context.** After embeddings moved in-process, enrichment was the only stage
left needing the network — and it was the worst one to depend on. Its first
run over 727 segments took four passes to drain past routing failures and rate
limits, and one of those failures silently discarded every segment it had
already enriched.

**Decision.** `Qwen3-4B-Instruct-2507-4bit` through mlx-lm, in-process, as the
default chat backend on Apple silicon. The gateway stays available via
`MASHUP_CHAT_BACKEND=gateway`, and remains the default everywhere else because
mlx does not exist there. `Gateway` already satisfied the `ChatModel`
protocol, so there is no wrapper class to drift out of sync.

**Why.** Sixteen minutes for a full archive, offline, free, and reproducible —
the gateway routes to whichever provider is cheapest this minute, so two runs
of the same prompt are not necessarily the same model. Enrichment quality is
what every scoring term reads, which makes "the same model every time" worth
more here than raw capability.

**Consequences.**

- **The batching contract is `chat_json_many`, not one call at a time.** The
  two backends parallelise in completely different ways — the gateway wants
  concurrent HTTP requests, mlx wants one batched forward pass — and hiding
  that behind a list-in, list-out method keeps `enrich_segments` free of
  backend knowledge. Batching is worth 1.9x locally: 2.42s per segment one at
  a time, 1.30s at four.
- **Prompts go a window at a time rather than all at once**, so a run that
  takes minutes can report progress. A single callback at the end would be
  no use.
- **A `None` reply means one batch failed, not the run.** Both backends return
  it rather than raising, which is the same degradation the gateway path
  already had, now shared.
- **JSON parsing moved to `jsonreply.py`.** `response_format` is not portable
  across gateway providers and does not exist in mlx-lm at all, so both
  backends ask for JSON in the prompt and parse defensively. Duplicating that
  parser would have been the obvious way to let the two drift apart.

**Trade-off accepted.** mlx-lm is Apple-silicon only, so the default backend
is platform-dependent — the one piece of configuration in this project that
is not the same everywhere.

---

## 16. A weaker model is a better prompt test

**Context.** Running the local 4B model over the archive and diffing its
output against the gateway's, field by field, was meant to be a regression
check. It found something else.

**What it found.** The `entities` field was being filled with topic phrases.
Where the gateway extracted `["bettina", "arresti"]`, the local model returned
`["waste money", "bum housekeepers", "stay on the phone"]`, and elsewhere
`["bologna", "norway", "73", "65"]` — 2,697 distinct entities against the
gateway's 1,025, and 674 recurring against 258.

This matters because `entities` is the only thing `term_callback` matches on.
Phrases like "cooking" and "late return" recur across every episode of a
domestic-comedy quiz show, so the callback strategy would have been
confidently optimising noise — the same failure decision 14 had just fixed
from the other direction.

**Decision.** Rewrite the instruction rather than accept the model's output,
and validate the field rather than trust it. The prompt now says what
`entities` is not ("NOT a topic list and NOT keyword extraction... exclude
subjects, activities, descriptions, numbers"), gives one worked example with
its counter-example, and permits `[]`. Bare numeric strings are rejected in
code, since nothing numeric is ever what a later clip calls back to.

**Why it generalises.** The original instruction — "recurring proper names,
running gags and catchphrases" — read as perfectly clear, and a large model
followed it. A 4B model did not, because the instruction described the target
without excluding the obvious wrong answer. The larger model had been quietly
covering for an underspecified prompt.

And it was only *quietly*: on the same segments the gateway had also offered
`"menial jobs"` and `"standing bar"` as entities. The tightened prompt is
better for both backends; the weak model simply made the flaw impossible to
miss. Diffing two models on the same input is worth more as a prompt review
than as a quality gate.

**Result.** 1,356 distinct entities and 296 recurring, against 2,697 and 674
before the rewrite and the gateway's 1,025 and 258. The top of the list is
people, sponsors and catchphrases rather than topic phrases.

**Trade-off accepted.** A 4B model is still weaker than whatever the gateway
routes to, and two fields remain measurably worse: `required_context` is
non-empty for 100% of segments against the gateway's 76%, and `energy` sits
at median 0.70 in a 0.40–0.90 band against 0.50 in 0.10–0.90. A field true of
every segment carries no information, so this is the same defect as a pinned
term arriving from the data side. Neither has been checked against a human
judgement; both are recorded in PROJECT_STATUS rather than assumed harmless.

---

## 17. The blind study was measuring noise, and nothing objected

**Context.** A five-condition set had been generated, rendered, counterbalanced
and prepared for viewers on the brief *"seven minutes on airline travel"*,
against twenty episodes of a 1950s quiz show. The `relevance` term read
0.41–0.46 for all five conditions — including the random control — which had
been logged across three runs as an undiagnosed flat term.

**What was actually wrong.** Not the term. The archive contains one segment
mentioning "airline", eight mentioning "plane" and none mentioning
"stewardess". There was nothing to retrieve.

The reason nothing caught it is that cosine similarity from an asymmetric
encoder has a floor far above zero. Measured with 40 nonsense probes against
this corpus, meaningless text averages **0.434** over its ten best matches.
The airline brief managed 0.459. `"quantum chromodynamics and lattice gauge
theory"` scored *higher* than `"airline travel"`. Every relevance figure the
project had recorded was sitting in a band where random letters score 0.43.

**Decision.** Measure the floor rather than pick a threshold, the same move
already made for [redundancy and flow calibration](#12-calibrate-similarity-thresholds-from-the-corpus).
`Retriever.coverage` embeds deterministic junk, takes what it earns for free,
and reports the real brief's lift over it. `mashup coverage` exposes it and
`mashup experiment` refuses a brief that fails it.

**Why a fixed cosine could never have worked.** The floor is a property of the
encoder *and* the corpus together. It is not portable across either, which is
exactly why the number had to be measured at runtime instead of written down.

**What it cost to find.** The pipeline had produced five plausible MP4s, a
counterbalanced rating sheet and a withheld key. Every stage reported success.
The only signal was a term reading flat, and that had been filed as a scoring
defect for three runs rather than as the archive answering a question it had no
material for.

---

## 18. Comparing five variants built from different clips cannot test ordering

**Context.** The product claim is that structure-aware *sequencing* beats
retrieve-and-join. The five-condition experiment was the test of it.

**What the measurement showed.** The five variants barely share material. On
Jaccard over clip sets, the chronological cut overlapped the other four
conditions by 0.00–0.05. A viewer preferring it would have been responding to
selection at least as much as to order, and the study had no way to separate
the two.

Two further decompositions made the point sharper:

- Of each objective's weight, the share that can see order at all is 0.32 for
  chronological, 0.54 for escalation, 0.56 for callback — and **0.00 for both
  baselines**, whose profile is relevance plus duration. The headline gap
  between 0.742 and 0.535 was partly two objectives measuring different things.
- Four of the eight terms varied by less than 0.10 across all five conditions.
  They supplied 45–67% of each AI score and **100%** of both baseline scores.

**Decision.** Add a matched pair: one clip set, two orders, same weight
profile, scored by the same function. `mashup experiment --matched`. A
preference there is attributable to sequencing and nothing else. Documented in
[experiment-matched.md](experiment-matched.md).

**The encouraging half.** Against 1000 shuffles of its own clips, the planner's
order sat at the 99.4th, 99.9th and 100th percentile for the three AI
strategies. The beam search really is optimising order rather than dressing up
a retrieval result — the ordering mechanism works. What was missing was an
experiment able to ask a human about it.

**A bug the new design exposed immediately.** `plan` applied a 6% penalty for
ending on a clip the model says cannot end; `rescore` did not. The matched pair
runs one arm through each, so an arbitrary shuffle outscored the planner's own
output on a 6% artefact while being worse on every order-sensitive term. The
penalty now lives in `ending_penalty` and both paths call it. This had been
latent since `rescore` was written, silently inflating every human-edited
timeline's score.
