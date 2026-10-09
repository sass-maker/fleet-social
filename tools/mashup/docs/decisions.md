# Decision log

One entry per real decision taken while building mashup. Each records what the
situation was, what was chosen, why, and what was knowingly given up. The
pipeline these decisions sit inside is drawn in
[`openspec/changes/build-mashup-mvp/design.md`](../openspec/changes/build-mashup-mvp/design.md).

---

## 1. SQLite with brute-force numpy, not pgvector

**Context.** The planner needs cosine similarity over every segment in an
archive, repeatedly: once per retrieval, and again pairwise inside every beam
scoring pass. The obvious reach is a vector database.

**Decision.** Store embeddings as float32 blobs in SQLite and scan them with
numpy. `Retriever` normalises the whole matrix once at construction and every
similarity is a matmul against it.

**Why.** One creator's archive is order 10³ segments. An exact scan of a
3k × 768 matrix is sub-millisecond — faster than the network hop to a vector
service would be. pgvector would add a service dependency, a migration story
and a local-dev prerequisite in exchange for nothing measurable.

**Trade-off accepted.** This does not scale to a multi-tenant corpus. The stated
revisit trigger is a single archive above ~10⁵ segments; below that the
decision is not close.

---

## 2. Segmentation is deterministic first, LLM-assisted second

**Context.** A subtitle line is the wrong cut unit — cutting on one orphans a
punchline from its setup. The alternatives were an LLM boundary pass over every
transcript, or a cheap structural heuristic.

**Decision.** `build_atoms` groups cues on pauses and speaker changes; an atom
never spans a long silence, so a boundary between atoms is always a safe cut.
`group_atoms` then merges atoms toward a target segment length, closing at the
longest nearby pause and favouring atoms that open a new thought.

**Why.** It is free, deterministic, and keeps the whole pipeline runnable with
no model access at all. An LLM pass over every transcript is the single most
expensive thing the project could do, and it would be paid on every re-ingest.

**Trade-off accepted.** Pauses cannot distinguish a mid-story breath from the
end of a story, so this will sometimes cut a bit in half. The safety net is
`required_context` from enrichment: a fragment needing prior setup gets flagged,
and the planner either satisfies it or is penalised for using it. That is a
better trade than paying for boundary inference everywhere. One residual: the
`splitter` module docstring still advertises a `refine_boundaries` LLM pass that
was never written.

---

## 3. One shared beam search, three weight profiles

**Context.** Three strategies — chronological, escalation, callback — plus two
baselines. The tempting shape is three bespoke planners.

**Decision.** `plan()` runs a single beam search. The strategies differ in
exactly two ways: their entry in `WEIGHT_PROFILES`, and whether they impose the
chronological ordering constraint. The baselines use the same
`score_sequence()` code.

**Why.** The comparison *is* the deliverable. Three bespoke planners would make
a win unattributable — was escalation better because the objective is right, or
because that planner got more attention? Sharing the machinery is what makes
the blind test mean something.

**Trade-off accepted.** No strategy gets a bespoke optimisation it might
deserve. Chronological in particular is running a beam search over a decision
that is largely forced, which is wasteful; the weights compensate by shifting
mass onto the terms selection can still influence.

---

## 4. `duration_fit` is excluded during beam search

**Context.** A naive objective evaluated on a partial sequence scores a
two-clip prefix of a seven-minute set as a catastrophic duration miss.

**Decision.** `_search_weights()` zeroes `duration_fit` and renormalises the
remaining weights for the search phase. The full profile, including
`duration_fit`, is restored for final scoring of finished beams.

**Why.** Otherwise every promising sequence is pruned in its infancy for the
crime of not being finished yet. Duration is a property of a completed
sequence, not of a prefix.

**Trade-off accepted.** The search is not optimising the objective it is finally
judged on, so the beam can carry sequences that later lose on duration. The
`DURATION_CEILING` and `DURATION_TOLERANCE` guards bound the damage.

---

## 5. Eight separate scoring terms, all surfaced

**Context.** The objective could have been one opaque learned score, or a
handful of blended heuristics.

**Decision.** Eight terms — relevance, context completeness, non-repetition,
progression, escalation, callback, duration fit, source diversity — each
normalised to 0..1, each independently unit-tested, all written into the EDL
alongside the weight profile that combined them.

**Why.** Two reasons, both about honesty. A creator who gets a bad mashup can
see that `context_completeness` was 0.4 rather than guessing at it. And an
ablation is only meaningful if the terms are separable in the first place.

**Trade-off accepted.** The weights are hand-set priors, not learned — a
hypothesis, not a result. The experiment is what tests them.

Two term-level calls worth recording:

- **`context_completeness` uses a lenient cosine threshold.**
  `required_context` is free text, so satisfaction is tested by embedding the
  prerequisite and checking whether any earlier clip exceeds a threshold. It is
  set low on purpose: a false "missing context" discards a good clip, while a
  false "covered" costs a moment of mild confusion. The asymmetry favours the
  cheaper error.
- **`callback` only counts a shared entity across a gap of two or more clips.**
  Adjacent clips about the same thing are continuation, not a callback. Without
  the gap requirement the term would reward exactly the topic-clustering that
  `non_repetition` is trying to punish.

---

## 6. Cuts snap outward, never inward

**Context.** Planner boundaries come from subtitle timings, which are
approximate. Cutting exactly on them clips the first or last syllable.

**Decision.** `snap_boundaries` moves a start earlier and an end later, and
`_pick` gives *any* candidate on the outward side priority over *every* inward
one — even a nearer inward candidate loses. Silence midpoints are preferred over
subtitle-gap midpoints; if nothing falls in the window, the boundary is left
alone.

**Why.** Clipping the first syllable of a punchline is the single most audible
failure a tool like this can produce. Widening a cut only adds a beat of air.
The two errors are not symmetric, so the rule should not be either.

**Trade-off accepted.** Clips run slightly long and can carry a stray word of
the neighbouring line. `MIN_CLIP_DURATION` plus a union fallback keeps a bad
snap from ever collapsing a clip.

---

## 7. Audio-only clips render over a neutral card

**Context.** Podcast archives have no video track. A mixed archive can have both.

**Decision.** Rather than forking the renderer into audio and video paths,
audio-only clips are composited over a static dark card at the target
resolution and frame rate, and clips with no audio get `anullsrc`. Every
intermediate ends up with identical codecs, resolution, frame rate, sample rate
and timebase.

**Why.** The concat demuxer only joins streams whose parameters already match.
Uniform intermediates make the join a lossless `-c copy`, make a mixed archive
concatenate without special cases, and guarantee the output is always a
playable MP4.

**Trade-off accepted.** Encoding a static colour source costs real CPU for
podcast archives that will never show a pixel of it. Cheaper than maintaining
two renderers.

Related: loudness normalisation is applied **per clip during extraction**, not
once over the finished timeline. Clips come from episodes recorded years apart;
normalising the concatenation would faithfully preserve every level jump
between them.

---

## 8. Everything goes through the free-ai gateway, not a provider key

**Context.** The pipeline needs a chat model for brief parsing and enrichment
and an embedding model for retrieval.

**Decision.** One `Gateway` client against the fleet free-ai gateway
(OpenAI-compatible). `config.py` holds a gateway key and nothing else. Default
chat model is `auto`; the embedding model must be explicit because the gateway
rejects `auto` there.

**Why.** No provider credentials in this repo, one place to change routing, and
fleet-level cost visibility. It also forces a useful discipline: because the
gateway routes to whichever model is cheapest right now, the code cannot assume
any particular model's behaviour.

**Trade-off accepted.** Model behaviour is non-deterministic across runs, which
is why `chat_json` does not send `response_format` (some routed models reject
it) and instead parses defensively — fenced blocks, prose wrappers, outermost
bracket span — with a repair round-trip. Two further consequences:

- **A content-addressed disk cache is mandatory, not an optimisation.**
  Enrichment gets re-run constantly while tuning everything downstream of it; a
  cached rerun must cost nothing. It is also what lets the editor rebuild the
  planner's scoring context offline.
- **Enrichment degrades per item, never per batch.** A returned item is matched
  by echoed id, falling back to position; an item that fails validation yields
  neutral metadata for that segment alone; an unknown `role` degrades to
  `development`. One sloppy field must not cost five segments' worth of tokens.

---

## 9. Public-domain Groucho corpus, not YouTube

**Context.** Development needs a real archive: one creator, many recordings,
comedy structure. The obvious source is YouTube.

**Decision.** `ybylcollection` on archive.org — *You Bet Your Life* with
Groucho Marx, 42 MPEG4 episodes under Public Domain Mark 1.0.
`scripts/fetch_archive.py` downloads it and **enforces** the licence position:
it refuses any item whose licence contains `-nd`, refuses missing or
unrecognised licences, and writes a `PROVENANCE.json` with the licence and
per-file md5s.

**Why.** Public domain means no ToS question, no copyright question, and
derivatives permitted without permission or attribution. It is also a genuinely
good product fit: one creator, one archive, and a format built on running gags —
which is what gives the callback strategy something real to find. Encoding the
gate in the fetcher rather than in a README means the rule survives the next
person who runs it.

**Trade-off accepted.** 1950s broadcast audio is hard for whisper, and segment
quality is bounded by transcript quality. If enrichment produces mush, a larger
whisper model is the first lever. The corpus also ships no subtitles at all, so
every episode costs a local transcription pass.

---

## 10. Every expensive stage is separately resumable

**Context.** Transcription and enrichment cost real money and minutes. Planning
is the stage that gets iterated fifty times.

**Decision.** Each stage writes to the SQLite store and is independently
re-runnable: `ingest` reuses an existing transcript on disk, `enrich` only
processes segments with no summary, `embed` only processes segments with no
vector, silence detection caches to a sidecar keyed on file identity and
detection parameters, and rendered clip intermediates cache on the fields that
affect pixels.

**Why.** This is not tidiness. It is what makes the cheap stage cheap to iterate
on, which is the whole working loop of the project.

**Trade-off accepted.** More cache-invalidation surface — five separate keying
schemes, each with its own way to go stale. Each one is keyed explicitly
(recipe version, file mtime and size, detection parameters) rather than by
timestamp, which is the mitigation.

---

## 11. Stdlib `http.server` for the editor backend

**Context.** The editor needs an HTTP backend for EDL read/write, candidate
search, segment detail and media preview.

**Decision.** `http.server.ThreadingHTTPServer`, loopback binds only, refusing
any non-loopback host at construction. The archive is snapshotted into memory
at startup. Media is served whole with `Range` support, addressed by
`source_id` against an allow-list — no user string ever reaches the filesystem.

**Why.** A single-operator local tool does not need FastAPI. The snapshot
sidesteps SQLite's cross-thread connection problem and makes candidate ranking a
numpy matmul over memory. Range support is not optional: without `206`
responses `<video>` cannot seek, and a preview that cannot seek to
`render_start` is useless.

**Trade-off accepted.** Edits to the archive during a session are invisible
until restart, and handler code is more verbose than a framework's. Both are
acceptable at one operator and order 10³ segments.

Related: when the planner's query context cannot be rebuilt, the editor
recomputes only the four terms computable from the sequence alone — escalation,
callback, duration fit, source diversity — carries the rest forward unchanged,
and *says so* in the response. Showing a fabricated relevance number would be
worse than showing a stale one.

---

Decisions 12–14, covering the local embedding backend, threshold calibration
and the callback strategy's candidate pool, live in
[`decisions-retrieval.md`](decisions-retrieval.md).
