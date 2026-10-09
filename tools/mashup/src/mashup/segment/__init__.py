"""Segment stage: cues -> self-contained segments, then LLM understanding."""

from mashup.segment.enrich import enrich_segments
from mashup.segment.splitter import build_atoms, group_atoms, split_source

__all__ = ["build_atoms", "enrich_segments", "group_atoms", "split_source"]
