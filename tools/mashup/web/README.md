# mashup editor (web)

The transcript timeline where a creator overrules the planner: reorder, remove,
replace, extend and preview the clips in an EDL, then export the corrected
edit-decision JSON.

Astro is the page shell. The timeline itself is a single React island
(`src/components/Timeline.tsx`) because every control on it mutates one shared
document — splitting that across islands would mean synchronising state across
roots for no gain.

## Prerequisites

- Node 20+ and `pnpm`
- An EDL to edit: `output/chronological.json`, written by `mashup build`
- The Python backend: `mashup serve` (see `src/mashup/serve.py`)

## Develop

Two processes. Backend first:

```bash
# from the repo root
uv run mashup serve output/chronological.json
# -> http://127.0.0.1:8765
```

Then the Astro dev server, with hot reload:

```bash
cd web
pnpm install     # first time only
pnpm dev
# -> http://127.0.0.1:4321
```

Open **http://127.0.0.1:4321**. Edits are written straight back to the EDL file
you passed to `mashup serve`, so point it at a copy if you want a scratch pad.

### How the proxy works

The page is served by Astro on `:4321`; the data lives behind Python on `:8765`.
`astro.config.mjs` proxies everything under `/api` to the backend through Vite's
dev-server proxy:

```js
vite: { server: { proxy: { '/api': { target: 'http://127.0.0.1:8765' } } } }
```

Two consequences worth knowing:

- **Same origin.** The browser only ever talks to `:4321`, so there is no CORS
  configuration anywhere and none is needed.
- **Range requests pass through.** `<video>` seeking depends on `206 Partial
  Content` replies from `/api/media/...`; the Vite proxy forwards `Range` and
  the partial response untouched.

Backend on a different port? `MASHUP_API=http://127.0.0.1:9000 pnpm dev`.

## Build

```bash
cd web
pnpm build      # -> web/dist
pnpm check      # astro check: types for .astro + .tsx
```

`mashup serve` serves `web/dist` at `/` whenever it exists, so after a build the
editor runs from a single origin with no Node process at all:

```bash
uv run mashup serve output/chronological.json
# -> http://127.0.0.1:8765  (UI + API)
```

If `dist/` is missing, the backend answers `/` with a `503` telling you to run
`pnpm build`. Set `MASHUP_WEB_DIST` to serve a build from elsewhere.

## Using it

Every card is one clip: position, `source_id`, source timecode (`mm:ss – mm:ss`),
duration, role badge, energy meter, and the transcript. The transcript is the
timeline — the text is the thing you read to judge the cut.

| Control | What it does |
| --- | --- |
| **remove** | drops the clip |
| **↑ / ↓** | reorders |
| **replace** | searches the archive (`/api/candidates`) and swaps the clip, keeping the full transcript |
| **extend** | pulls the adjacent segment from the same source into the clip, widening the render range |
| **preview** | plays exactly `render_start → render_end` in a `<video>` |
| **undo** | reverts the last edit (50 deep) |
| **export JSON** | downloads the current EDL |

Keyboard: <kbd>j</kbd>/<kbd>k</kbd> move between clips, <kbd>J</kbd>/<kbd>K</kbd>
reorder, <kbd>x</kbd> remove, <kbd>r</kbd> replace, <kbd>e</kbd> extend,
<kbd>p</kbd> preview, <kbd>u</kbd> undo, <kbd>Esc</kbd> close a panel.

Every edit is `PUT` to the backend, which re-validates it with pydantic, writes
it atomically, and returns the rescored document — the score and term bars in
the header are the server's answer, not a local guess.

### Why some term bars are hatched

Rescoring `relevance`, `context_completeness`, `non_repetition` and
`progression` needs the query embedding the planner used, which the EDL does not
persist. When the backend can rebuild it (gateway reachable or its on-disk cache
warm) every term is recomputed and the header says *full rescore*. Otherwise it
recomputes only `escalation`, `callback`, `duration_fit` and `source_diversity`,
says *partial rescore*, and hatches the carried-over bars rather than showing a
number it did not compute.

The replace panel degrades the same way: with embeddings it ranks by cosine
similarity, without them it falls back to word matching and says so.

## Layout

```
web/
  astro.config.mjs        React integration + /api proxy
  src/pages/index.astro   the shell (one page)
  src/components/
    Timeline.tsx          the island: document state, saving, undo, keyboard
    ClipCard.tsx          one clip row
    ReplacePanel.tsx      candidate search
    ExtendPanel.tsx       adjacent-segment merge
    ClipPlayer.tsx        range-seeking preview
    ScoreHeader.tsx       prompt, duration vs target, score, term bars
    EnergyMeter.tsx
  src/lib/{api,types,format}.ts
  src/styles/app.css      plain CSS, prefers-color-scheme, no external assets
```

No Tailwind, no CDN, no web fonts: the editor has to work on a laptop with the
network off, because so does the rest of the pipeline.
