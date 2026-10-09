"""EDL persistence and the human-readable review transcript.

The EDL is the hand-off point between the planner, the CLI reviewer and the
Astro editor, so the on-disk form is pretty-printed and key-sorted: it lands in
git diffs and people read it.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from mashup.models import EDL

_WRAP = 84
_INDENT = "    "


def save_edl(edl: EDL, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(edl.model_dump(mode="json"), indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8")


def load_edl(path: Path) -> EDL:
    return EDL.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def edl_to_transcript(edl: EDL) -> str:
    """Render the EDL as the plain-text preview the CLI prints for review.

    One header block, then per clip a locator line followed by the wrapped
    segment text — enough to judge the cut without opening a video player.
    """
    lines = [
        f'{edl.strategy}: "{edl.prompt}"',
        f"{len(edl.clips)} clips, {_mmss(edl.duration)} (target {_mmss(edl.target_duration)})",
    ]
    for clip in edl.clips:
        lines.append("")
        lines.append(
            f"[{clip.index:02d}] {clip.source_id} "
            f"@ {_mmss(clip.render_start)}-{_mmss(clip.render_end)} "
            f"({clip.render_duration:.0f}s, {clip.role.value}, {clip.energy:.2f})"
        )
        text = " ".join(clip.text.split())
        if text:
            lines.append(
                textwrap.fill(text, width=_WRAP, initial_indent=_INDENT, subsequent_indent=_INDENT)
            )
    return "\n".join(lines) + "\n"
