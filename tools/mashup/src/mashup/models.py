"""Core data contract for the mashup pipeline.

Every stage reads and writes these types. The two that cross process
boundaries are `Segment` (persisted in SQLite) and `EDL` (the edit-decision
list written to disk, read by the renderer and the Astro editor).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EDL_VERSION = 1


class Role(StrEnum):
    """What a segment does inside a bit.

    Ordering matters: `Role.arc_index` is used by the escalation planner to
    reward sequences that move forward through a comedic arc.
    """

    SETUP = "setup"
    DEVELOPMENT = "development"
    PUNCHLINE = "punchline"
    CALLBACK = "callback"
    CLOSER = "closer"
    ASIDE = "aside"

    @property
    def arc_index(self) -> int:
        return _ARC_INDEX[self]


# ASIDE and CALLBACK sit off the main arc — they are placed by other signals
# (source diversity, callback matching) rather than by arc progression.
_ARC_INDEX: dict[Role, int] = {
    Role.SETUP: 0,
    Role.DEVELOPMENT: 1,
    Role.PUNCHLINE: 2,
    Role.CLOSER: 3,
    Role.CALLBACK: 2,
    Role.ASIDE: 1,
}


class Cue(BaseModel):
    """One subtitle line, as parsed from SRT/VTT."""

    index: int
    start: float = Field(ge=0, description="seconds from start of source")
    end: float = Field(ge=0)
    text: str
    speaker: str | None = None

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start")
        if start is not None and v < start:
            raise ValueError(f"cue end {v} precedes start {start}")
        return v

    @property
    def duration(self) -> float:
        return self.end - self.start


class Source(BaseModel):
    """One ingested archive item (an episode, a set, a podcast instalment)."""

    id: str
    path: str = Field(description="absolute path to the media file")
    title: str
    duration: float
    has_video: bool
    subtitle_path: str | None = None
    subtitle_origin: Literal["provided", "transcribed"] = "provided"
    # Position in the creator's archive. Drives the chronological planner and
    # is the tiebreaker for callback direction (a callback must follow its referent).
    recorded_at: str | None = None
    ordinal: int = 0


class SegmentMeta(BaseModel):
    """LLM-extracted understanding of a segment. Mirrors the PRD schema."""

    topic: list[str] = Field(default_factory=list)
    role: Role = Role.DEVELOPMENT
    summary: str = ""
    required_context: list[str] = Field(
        default_factory=list,
        description="Free-text prerequisites a viewer must already know. "
        "Empty means the segment is self-contained.",
    )
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    can_open: bool = False
    can_end: bool = False
    # Recurring names/phrases/running gags. The callback planner matches on these.
    entities: list[str] = Field(default_factory=list)


class Segment(BaseModel):
    """A self-contained unit of material: a complete setup, story, or bit.

    Boundaries are chosen so that cutting here does not orphan a punchline.
    """

    id: str
    source_id: str
    start: float
    end: float
    text: str
    cue_start: int
    cue_end: int
    meta: SegmentMeta = Field(default_factory=SegmentMeta)
    # Populated by the retrieval stage; not stored inline in the EDL.
    embedding: list[float] | None = None
    # Planning can join adjacent stored segments into one transient editorial
    # bit. Stored segments leave these empty; synthetic bits retain both the
    # complete source provenance and the retrieved anchor that created them.
    member_segment_ids: list[str] = Field(default_factory=list)
    anchor_segment_id: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def material_ids(self) -> frozenset[str]:
        """Stored source material represented by this planning unit."""
        return frozenset(self.member_segment_ids or [self.id])


class ScoreTerms(BaseModel):
    """Why a sequence scored what it did — surfaced in the EDL so the ranking
    is inspectable rather than a black box."""

    relevance: float = 0.0
    context_completeness: float = 0.0
    non_repetition: float = 0.0
    progression: float = 0.0
    escalation: float = 0.0
    callback: float = 0.0
    duration_fit: float = 0.0
    source_diversity: float = 0.0

    def total(self, weights: dict[str, float]) -> float:
        return sum(getattr(self, k) * w for k, w in weights.items())


class ShortReview(BaseModel):
    """Editorial evidence for one exact short-form source window."""

    can_open: bool = False
    can_end: bool = False
    opening_quote: str = ""
    ending_quote: str = ""
    hook_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    payoff_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class VisualInsert(BaseModel):
    """One provenance-backed still shown over a clip's existing audio."""

    mode: Literal["still", "motion"] = "still"
    start: float = Field(ge=0, description="seconds from the start of the rendered clip")
    end: float = Field(gt=0)
    source_path: str
    source_time: float = Field(default=0.0, ge=0)
    source_title: str
    source_url: str = ""

    @field_validator("end")
    @classmethod
    def _visual_end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start")
        if start is not None and v <= start:
            raise ValueError("visual end must follow its start")
        return v


class Clip(BaseModel):
    """One segment placed in a sequence, with the exact cut points to render."""

    index: int
    segment_id: str
    # Ordered stored segments used by a context-repaired editorial bit.
    # Empty on legacy EDLs, where `segment_id` remains the sole member.
    segment_ids: list[str] = Field(default_factory=list)
    source_id: str
    # Human-readable archive title for timeline provenance. Legacy EDLs fall
    # back to `source_id`.
    source_title: str = ""
    source_path: str
    # Segment boundaries as understood by the planner.
    start: float
    end: float
    # Boundaries after snapping to a nearby silence/sentence edge. These are
    # what ffmpeg actually cuts on.
    render_start: float
    render_end: float
    text: str
    summary: str
    role: Role
    energy: float
    topics: list[str] = Field(default_factory=list)
    # Optional provenance-backed frames shown while this clip's audio continues.
    visuals: list[VisualInsert] = Field(default_factory=list)
    transition: Literal["cut", "crossfade"] = "cut"
    # Set when a human edits the timeline, so re-renders keep provenance.
    edited: bool = False
    note: str | None = None

    @property
    def render_duration(self) -> float:
        return self.render_end - self.render_start


class EDL(BaseModel):
    """Edit-decision list — the internal format and the editor's document."""

    version: int = EDL_VERSION
    strategy: str
    prompt: str
    target_duration: float
    generated_at: str
    clips: list[Clip] = Field(default_factory=list)
    score: float = 0.0
    terms: ScoreTerms = Field(default_factory=ScoreTerms)
    weights: dict[str, float] = Field(default_factory=dict)
    # Similarity thresholds this score was computed against. Recorded because
    # they are measured from the candidate pool, so an editor rescoring later
    # against the whole archive would otherwise silently use different cuts
    # and report a score that cannot be compared with the one it replaced.
    calibration: dict[str, float | str] = Field(default_factory=dict)
    # Human-readable reasons the planner made the choices it made.
    rationale: list[str] = Field(default_factory=list)
    # Exact-window short-form review. Long-form and legacy EDLs leave this empty.
    short_review: ShortReview | None = None

    @property
    def duration(self) -> float:
        return sum(c.render_duration for c in self.clips)
