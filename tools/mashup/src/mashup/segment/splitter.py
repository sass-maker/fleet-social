"""Turn subtitle cues into self-contained segments.

A subtitle line is the wrong unit — cutting on one orphans punchlines from
their setups. This module builds up in two deterministic steps:

1. `build_atoms` — groups cues on speech pauses and speaker changes into
   5-30s atoms. An atom never spans a long silence, so it is always safe to
   cut *between* atoms.
2. `group_atoms` — merges atoms toward a target segment length, closing at
   the longest nearby pause and preferring an atom that opens a new thought.

Both steps are free and need no model access, which keeps the pipeline
runnable offline. The cost is that pauses alone cannot distinguish a
mid-story breath from the end of a story, so this will sometimes split a bit
in half. `required_context` from the enrichment stage is the safety net: a
fragment that needs prior setup gets flagged, and the planner either
satisfies it or is penalised for using it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mashup.models import Cue, Segment

# A pause longer than this reads as a beat between thoughts rather than a
# breath inside one.
DEFAULT_PAUSE_GAP = 1.1
DEFAULT_MAX_ATOM = 30.0
DEFAULT_MIN_SEGMENT = 20.0
DEFAULT_TARGET_SEGMENT = 55.0
DEFAULT_MAX_SEGMENT = 120.0
# Conversational speech runs 2-3 words/second. Anything below this is a
# transcriber artefact over non-speech audio, not quiet dialogue. See
# `drop_non_speech`.
MIN_WORDS_PER_SECOND = 0.5

# Digits count: "I bet you 25 dollars" is five spoken words.
_WORD = re.compile(r"[A-Za-z']+|\d+")

# Phrases that typically open a new thought. Used only as a tiebreaker when
# choosing where to split an over-long run of atoms.
_OPENER = re.compile(
    r"^(so|now|okay|ok|alright|anyway|and then|by the way|you know what|here's|listen|"
    r"speaking of|one more thing|let me tell you|i remember|there was)\b",
    re.IGNORECASE,
)


@dataclass
class Atom:
    """A pause-delimited run of cues. The smallest safe cut unit."""

    index: int
    cues: list[Cue] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.cues[0].start

    @property
    def end(self) -> float:
        return self.cues[-1].end

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(c.text.strip() for c in self.cues if c.text.strip())

    @property
    def speaker(self) -> str | None:
        return self.cues[0].speaker

    @property
    def opens_thought(self) -> bool:
        return bool(_OPENER.match(self.text))


def build_atoms(
    cues: list[Cue],
    *,
    pause_gap: float = DEFAULT_PAUSE_GAP,
    max_atom: float = DEFAULT_MAX_ATOM,
) -> list[Atom]:
    """Group cues into pause-delimited atoms."""
    atoms: list[Atom] = []
    current: list[Cue] = []

    def flush() -> None:
        if current:
            atoms.append(Atom(index=len(atoms), cues=list(current)))
            current.clear()

    for cue in cues:
        if not cue.text.strip():
            continue
        if current:
            gap = cue.start - current[-1].end
            span = cue.end - current[0].start
            speaker_changed = cue.speaker != current[0].speaker
            if gap >= pause_gap or span > max_atom or speaker_changed:
                flush()
        current.append(cue)
    flush()
    return atoms


def _gap_between(a: Atom, b: Atom) -> float:
    return max(0.0, b.start - a.end)


def group_atoms(
    atoms: list[Atom],
    *,
    min_segment: float = DEFAULT_MIN_SEGMENT,
    target_segment: float = DEFAULT_TARGET_SEGMENT,
    max_segment: float = DEFAULT_MAX_SEGMENT,
) -> list[list[Atom]]:
    """Greedily group atoms into segment-sized runs.

    Accumulates until the run reaches `target_segment`, then closes at the
    most natural nearby edge — the longest pause, preferring an atom that
    opens a new thought. Hard-closes at `max_segment`.
    """
    if not atoms:
        return []

    groups: list[list[Atom]] = []
    run: list[Atom] = []

    def run_duration() -> float:
        return run[-1].end - run[0].start if run else 0.0

    for atom in atoms:
        # A single atom longer than max_segment cannot be split further
        # without cutting mid-speech, so it stands alone.
        if not run and atom.duration >= max_segment:
            groups.append([atom])
            continue

        prospective = (atom.end - run[0].start) if run else atom.duration
        if run and prospective > max_segment:
            groups.append(run)
            run = [atom]
            continue

        run.append(atom)

        if run_duration() >= target_segment:
            cut_at = _best_cut_index(run, min_segment)
            if cut_at is not None and cut_at < len(run):
                groups.append(run[:cut_at])
                run = run[cut_at:]

    if run:
        # Fold a too-short trailing run into the previous group rather than
        # emitting a fragment that cannot stand alone.
        if groups and run_duration() < min_segment:
            groups[-1].extend(run)
        else:
            groups.append(run)
    return groups


def _best_cut_index(run: list[Atom], min_segment: float) -> int | None:
    """Index in `run` where a new segment should start, or None to keep going."""
    best_idx: int | None = None
    best_score = -1.0
    for i in range(1, len(run)):
        if run[i - 1].end - run[0].start < min_segment:
            continue
        score = _gap_between(run[i - 1], run[i])
        if run[i].opens_thought:
            score += 0.75
        if run[i].speaker != run[i - 1].speaker:
            score += 0.25
        if score > best_score:
            best_score, best_idx = score, i
    return best_idx


def segments_from_groups(source_id: str, groups: list[list[Atom]]) -> list[Segment]:
    segments: list[Segment] = []
    for ordinal, group in enumerate(groups):
        cues = [c for atom in group for c in atom.cues]
        if not cues:
            continue
        text = " ".join(c.text.strip() for c in cues if c.text.strip())
        if not text:
            continue
        segments.append(
            Segment(
                id=f"{source_id}:{ordinal:04d}",
                source_id=source_id,
                start=cues[0].start,
                end=cues[-1].end,
                text=text,
                cue_start=cues[0].index,
                cue_end=cues[-1].index,
            )
        )
    return segments


def speech_density(segment: Segment) -> float:
    """Words per second of wall-clock time."""
    words = len(_WORD.findall(segment.text))
    return words / segment.duration if segment.duration > 0 else 0.0


def drop_non_speech(segments: list[Segment]) -> list[Segment]:
    """Remove segments that are transcription artefacts rather than content.

    Whisper emits a short stock phrase — most often "Thank you." — for a
    30-second window of music, applause, or silence. Measured on the dev
    archive these are 14% of all segments and the split is unambiguous:
    real speech sits at 1.9-3.3 words/second while the artefacts sit at 0.07.

    Left in, they waste an enrichment call each, pollute retrieval, and can
    be planned into a mashup as half a minute of dead air.
    """
    return [s for s in segments if speech_density(s) >= MIN_WORDS_PER_SECOND]


def split_source(
    source_id: str,
    cues: list[Cue],
    *,
    pause_gap: float = DEFAULT_PAUSE_GAP,
    min_segment: float = DEFAULT_MIN_SEGMENT,
    target_segment: float = DEFAULT_TARGET_SEGMENT,
    max_segment: float = DEFAULT_MAX_SEGMENT,
    filter_non_speech: bool = True,
) -> list[Segment]:
    """Deterministic cues -> segments for one source."""
    atoms = build_atoms(cues, pause_gap=pause_gap)
    groups = group_atoms(
        atoms,
        min_segment=min_segment,
        target_segment=target_segment,
        max_segment=max_segment,
    )
    segments = segments_from_groups(source_id, groups)
    # Filtering after numbering leaves gaps in the ordinals, which is
    # deliberate: segment ids then stay stable if the threshold is retuned.
    return drop_non_speech(segments) if filter_non_speech else segments
