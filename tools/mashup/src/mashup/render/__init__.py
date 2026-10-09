"""Render stage: snap cut points, persist EDLs, and cut the final MP4."""

from __future__ import annotations

from mashup.render.boundaries import detect_silences, snap_boundaries
from mashup.render.cut import render
from mashup.render.edl_io import edl_to_transcript, load_edl, save_edl

__all__ = [
    "detect_silences",
    "edl_to_transcript",
    "load_edl",
    "render",
    "save_edl",
    "snap_boundaries",
]
