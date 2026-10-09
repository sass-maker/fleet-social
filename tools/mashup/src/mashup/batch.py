"""Versioned local clip batches and their static operator review surface."""

# The generated standalone HTML keeps its CSS inline and intentionally readable as CSS.
# ruff: noqa: E501

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from mashup.models import EDL

BATCH_SCHEMA = "fleet.mashup-clip-batch.v1"
ReviewState = Literal["candidate", "shortlisted", "rejected", "posted"]


class BatchItem(BaseModel):
    index: int = Field(ge=1)
    id: str
    angle: str
    source_id: str
    source_title: str
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    score: float
    terms: dict[str, float]
    weights: dict[str, float] = Field(default_factory=dict)
    score_multiplier: float = 1.0
    hook_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    payoff_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    editorial_reason: str = ""
    summary: str
    transcript: str
    edl_path: str
    video_path: str | None = None
    captions_path: str | None = None
    review_state: ReviewState = "candidate"


class ClipBatch(BaseModel):
    schema_: Literal[BATCH_SCHEMA] = Field(default=BATCH_SCHEMA, alias="schema")
    id: str
    collection: str
    collection_name: str
    angle: str
    prompt: str
    generated_at: str
    render_profile: Literal["social"] = "social"
    width: int = 1080
    height: int = 1920
    source_policy: str
    items: list[BatchItem] = Field(min_length=3, max_length=5)

    model_config = {"populate_by_name": True}


def build_batch(
    edls: list[EDL],
    *,
    collection: str,
    collection_name: str,
    angle: str,
    prompt: str,
    source_policy: str,
    output_dir: Path,
) -> ClipBatch:
    if not 3 <= len(edls) <= 5:
        raise ValueError("a clip batch must contain between 3 and 5 EDLs")
    generated_at = datetime.now(UTC).isoformat()
    batch_id = f"{collection}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    items: list[BatchItem] = []
    for index, edl in enumerate(edls, start=1):
        if len(edl.clips) != 1:
            raise ValueError(f"batch EDL {index} must contain exactly one clip")
        clip = edl.clips[0]
        stem = f"clip-{index:02d}"
        video = output_dir / f"{stem}.mp4"
        captions = output_dir / f"{stem}.srt"
        weights = edl.weights
        weighted_score = sum(
            value * weights.get(name, 0.0) for name, value in edl.terms.model_dump().items()
        )
        score_multiplier = edl.score / weighted_score if weighted_score else 1.0
        items.append(
            BatchItem(
                index=index,
                id=stem,
                angle=angle,
                source_id=clip.source_id,
                source_title=clip.source_title or clip.source_id,
                source_start=clip.start,
                source_end=clip.end,
                duration_seconds=clip.render_duration,
                score=edl.score,
                terms=edl.terms.model_dump(),
                weights=weights,
                score_multiplier=score_multiplier,
                hook_strength=(edl.short_review.hook_strength if edl.short_review else None),
                payoff_strength=(edl.short_review.payoff_strength if edl.short_review else None),
                editorial_reason=(edl.short_review.reason if edl.short_review else ""),
                summary=clip.summary,
                transcript=clip.text,
                edl_path=f"{stem}.json",
                video_path=video.name if video.is_file() else None,
                captions_path=captions.name if captions.is_file() else None,
            )
        )
    return ClipBatch(
        id=batch_id,
        collection=collection,
        collection_name=collection_name,
        angle=angle,
        prompt=prompt,
        generated_at=generated_at,
        source_policy=source_policy,
        items=items,
    )


def save_batch(batch: ClipBatch, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(batch.model_dump(mode="json", by_alias=True), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def load_batch(path: Path) -> ClipBatch:
    return ClipBatch.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _timecode(seconds: float) -> str:
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"


def _item_html(item: BatchItem) -> str:
    source = html.escape(item.source_title)
    transcript = html.escape(item.transcript)
    video = (
        f'<video controls preload="metadata" aria-label="Preview clip {item.index} from {source}" '
        f'src="{html.escape(item.video_path)}"></video>'
        if item.video_path
        else '<div class="preview-pending">Render pending<br><small>Review the transcript and EDL.</small></div>'
    )
    terms = "".join(
        f'<li title="Contribution: {value * item.weights.get(name, 0.0):.3f}">'
        f"<span>{html.escape(name.replace('_', ' '))}</span>"
        f'<span class="term-value"><strong>{value:.2f}</strong>'
        f"<small>× {item.weights.get(name, 0.0):.2f}</small></span></li>"
        for name, value in item.terms.items()
    )
    multiplier = (
        f" · ending ×{item.score_multiplier:.2f}"
        if abs(item.score_multiplier - 1.0) >= 0.005
        else ""
    )
    editorial = (
        f'<p class="score-note">Short editorial · hook {item.hook_strength:.2f} · '
        f"payoff {item.payoff_strength:.2f} · {html.escape(item.editorial_reason)}</p>"
        if item.hook_strength is not None and item.payoff_strength is not None
        else ""
    )
    caption_link = (
        f'<a href="{html.escape(item.captions_path)}">captions</a>' if item.captions_path else ""
    )
    return f"""
      <article class="candidate" data-item="{html.escape(item.id)}">
        <div class="preview">{video}</div>
        <div class="evidence">
          <header>
            <span class="index">{item.index:02d}</span>
            <div>
              <p class="source">{source} · {_timecode(item.source_start)}–{_timecode(item.source_end)}</p>
              <h2>{html.escape(item.summary or item.angle.replace("-", " "))}</h2>
            </div>
            <div class="score"><strong>{item.score:.3f}</strong><span>0–1 score</span></div>
          </header>
          <p class="transcript">{transcript}</p>
          {editorial}
          <p class="score-note">Escalation profile · each signal shows value × weight{multiplier}</p>
          <ul class="terms">{terms}</ul>
          <footer>
            <div class="review-actions" role="group" aria-label="Review clip {item.index}">
              <button type="button" data-state="shortlisted" aria-pressed="false">Shortlist</button>
              <button type="button" data-state="rejected" aria-pressed="false">Reject</button>
              <button type="button" data-state="candidate" aria-pressed="true">Reset</button>
              <span class="review-state" aria-live="polite">undecided</span>
            </div>
            <nav aria-label="Clip {item.index} artifacts">
              <a href="{html.escape(item.edl_path)}">EDL JSON</a>
              {caption_link}
              {f'<a href="{html.escape(item.video_path)}" download>download</a>' if item.video_path else ""}
            </nav>
          </footer>
        </div>
      </article>"""


def review_html(batch: ClipBatch) -> str:
    items = "\n".join(_item_html(item) for item in batch.items)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>{html.escape(batch.collection_name)} clipping desk</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#f4f5f7; --surface:#fff; --surface-2:#eceef2; --border:#d3d6dd; --text:#16181d; --dim:#5d626d; --accent:#2b4fd8; --accent-soft:#e4e9ff; --ok:#14663a; --ok-soft:#e0f3e8; --danger:#b3261e; --danger-soft:#fbe6e4; --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace; --sans:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d0e11; --surface:#15171c; --surface-2:#1c1f26; --border:#2a2e37; --text:#e7e9ee; --dim:#98a0ad; --accent:#8aa8ff; --accent-soft:#1e2740; --ok:#6ed3a0; --ok-soft:#16301f; --danger:#ff7a70; --danger-soft:#38211f; }} }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; padding:24px 18px 64px; color:var(--text); background:var(--bg); font:13.5px/1.45 var(--sans); }}
    main {{ max-width:1080px; margin:auto; }}
    .desk-head {{ display:grid; grid-template-columns:1fr auto; gap:24px; align-items:end; padding:22px 24px; border:1px solid var(--border); border-radius:10px; background:var(--surface); }}
    .kicker,.source,.index,.score span,.batch-meta {{ font-family:var(--mono); }}
    .kicker {{ margin:0 0 8px; color:var(--accent); font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; }}
    h1 {{ margin:0; font-size:28px; letter-spacing:-.025em; }}
    .brief {{ max-width:70ch; margin:8px 0 0; overflow-wrap:anywhere; color:var(--dim); }}
    .batch-meta {{ display:grid; min-width:0; gap:4px; overflow-wrap:anywhere; color:var(--dim); font-size:11px; text-align:right; }}
    .review-progress {{ position:sticky; z-index:2; top:8px; display:flex; justify-content:space-between; gap:16px; margin:10px 0; padding:10px 14px; border:1px solid var(--border); border-radius:5px; background:color-mix(in srgb,var(--surface) 94%,transparent); box-shadow:0 4px 18px color-mix(in srgb,var(--bg) 78%,transparent); backdrop-filter:blur(8px); }}
    .review-progress span:last-child {{ color:var(--dim); }}
    .queue {{ display:grid; gap:10px; }}
    .candidate {{ display:grid; grid-template-columns:220px minmax(0,1fr); min-height:300px; overflow:hidden; border:1px solid var(--border); border-radius:5px; background:var(--surface); }}
    .candidate[data-review="shortlisted"] {{ border-color:var(--ok); }}
    .candidate[data-review="rejected"] {{ border-color:var(--danger); background:color-mix(in srgb,var(--surface) 94%,var(--danger-soft)); }}
    .preview {{ display:grid; min-height:300px; place-items:center; background:#0b0c0f; }}
    video {{ display:block; width:100%; height:100%; max-height:390px; object-fit:contain; background:#000; }}
    .preview-pending {{ color:#98a0ad; text-align:center; }}
    .evidence {{ display:flex; min-width:0; flex-direction:column; padding:16px; }}
    .evidence header {{ display:grid; grid-template-columns:38px minmax(0,1fr) auto; gap:10px; align-items:start; }}
    .index {{ color:var(--accent); font-weight:700; }}
    .source {{ margin:0; overflow:hidden; color:var(--dim); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }}
    h2 {{ margin:3px 0 0; overflow-wrap:anywhere; font-size:17px; text-transform:capitalize; }}
    .score {{ text-align:right; }} .score strong {{ display:block; font:700 17px/1 var(--mono); }} .score span {{ color:var(--dim); font-size:10px; }}
    .score-note {{ margin:0 0 8px; color:var(--dim); font:10px/1.4 var(--mono); }}
    .transcript {{ max-width:72ch; margin:18px 0; font-size:16px; line-height:1.55; }}
    .terms {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px 14px; margin:0; padding:0; list-style:none; }}
    .terms li {{ display:flex; justify-content:space-between; gap:8px; border-bottom:1px solid var(--border); color:var(--dim); font-size:11px; }}
    .term-value {{ display:inline-flex; gap:5px; align-items:baseline; white-space:nowrap; }} .terms strong {{ color:var(--text); font-family:var(--mono); }} .terms small {{ color:var(--dim); font:9px var(--mono); }}
    .evidence footer {{ display:flex; justify-content:space-between; gap:12px; align-items:center; margin-top:auto; padding-top:18px; }}
    button,a {{ min-height:44px; display:inline-flex; align-items:center; justify-content:center; border:1px solid var(--border); border-radius:5px; color:var(--text); background:var(--surface-2); font:600 12px/1 var(--sans); text-decoration:none; }}
    button {{ padding:8px 12px; cursor:pointer; }} a {{ padding:8px 11px; }} button:hover,a:hover {{ border-color:var(--accent); }} button:focus-visible,a:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
    .review-actions,nav {{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; }} .review-state {{ margin-left:4px; color:var(--dim); font:11px var(--mono); }}
    .candidate[data-review="shortlisted"] .review-state {{ color:var(--ok); }} .candidate[data-review="rejected"] .review-state {{ color:var(--danger); }}
    .boundary {{ margin:16px 2px 0; color:var(--dim); font-size:11px; }}
    @media (max-width:860px) {{ .desk-head {{ grid-template-columns:1fr; }} .batch-meta {{ text-align:left; }} .candidate {{ grid-template-columns:170px minmax(0,1fr); }} .terms {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .evidence footer {{ align-items:flex-start; flex-direction:column; }} }}
    @media (max-width:520px) {{ body {{ padding:10px 8px 40px; }} .desk-head {{ padding:16px; }} .review-progress {{ position:static; flex-direction:column; gap:2px; }} .candidate {{ grid-template-columns:1fr; }} .preview {{ min-height:100px; max-height:520px; }} .preview:has(video) {{ min-height:260px; }} .evidence {{ padding:14px; }} .evidence header {{ grid-template-columns:30px minmax(0,1fr); }} .score {{ grid-column:2; text-align:left; }} .source {{ white-space:normal; }} .transcript {{ font-size:15px; }} .terms {{ grid-template-columns:1fr 1fr; }} nav {{ flex-wrap:wrap; }} }}
  </style>
</head>
<body>
  <main>
    <header class="desk-head">
      <div><p class="kicker">Local operator desk · manual publishing</p><h1>{html.escape(batch.collection_name)} · {html.escape(batch.angle.replace("-", " "))}</h1><p class="brief">{html.escape(batch.prompt)}</p></div>
      <div class="batch-meta"><span>{len(batch.items)} candidates</span><span>1080 × 1920 social profile</span><span>{html.escape(batch.id)}</span></div>
    </header>
    <div class="review-progress" role="status" aria-live="polite"><strong data-progress>Reviewed 0/{len(batch.items)}</strong><span data-next>{len(batch.items)} remaining · decisions save locally</span></div>
    <section class="queue" aria-label="Clip candidates">{items}</section>
    <p class="boundary">{html.escape(batch.source_policy)} Review state stays in this browser; Mashup does not publish.</p>
  </main>
  <script>
    const storageKey = {json.dumps(f"mashup-review:{batch.id}")};
    let state = {{}};
    let storageRecovered = false;
    try {{ state = JSON.parse(localStorage.getItem(storageKey) || '{{}}'); }} catch {{ state = {{}}; storageRecovered = true; }}
    const progress = document.querySelector('[data-progress]');
    const next = document.querySelector('[data-next]');
    const updateProgress = () => {{
      const values = [...document.querySelectorAll('.candidate')].map((card) => card.dataset.review);
      const reviewed = values.filter((value) => value && value !== 'candidate').length;
      const shortlisted = values.filter((value) => value === 'shortlisted').length;
      progress.textContent = `Reviewed ${{reviewed}}/${{values.length}} · Shortlisted ${{shortlisted}}`;
      next.textContent = storageRecovered ? 'Saved review data was unreadable; reset safely' : reviewed === values.length ? 'Review complete · export remains manual' : `${{values.length - reviewed}} remaining · decisions save locally`;
    }};
    document.querySelectorAll('.candidate').forEach((card) => {{
      const id = card.dataset.item;
      const apply = (value) => {{
        card.dataset.review = value;
        card.querySelector('.review-state').textContent = value === 'candidate' ? 'undecided' : value;
        card.querySelectorAll('[data-state]').forEach((button) => {{
          button.setAttribute('aria-pressed', String(button.dataset.state === value));
        }});
      }};
      apply(state[id] || 'candidate');
      card.querySelectorAll('[data-state]').forEach((button) => button.addEventListener('click', () => {{
        const next = state[id] === button.dataset.state ? 'candidate' : button.dataset.state;
        state[id] = next; localStorage.setItem(storageKey, JSON.stringify(state)); apply(next);
        storageRecovered = false; updateProgress();
      }}));
    }});
    updateProgress();
  </script>
</body>
</html>
"""


def save_review_html(batch: ClipBatch, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(review_html(batch), encoding="utf-8")
    return path
