from __future__ import annotations

from conftest import make_cues

from mashup.segment.splitter import build_atoms, group_atoms, split_source


def test_atoms_break_on_long_pause():
    cues = make_cues(
        [
            (0.0, 2.0, "one"),
            (2.1, 4.0, "two"),  # 0.1s gap — same atom
            (8.0, 10.0, "three"),  # 4s gap — new atom
        ]
    )
    atoms = build_atoms(cues, pause_gap=1.1)
    assert len(atoms) == 2
    assert atoms[0].text == "one two"
    assert atoms[1].text == "three"


def test_atoms_break_on_speaker_change():
    cues = [
        *make_cues([(0.0, 2.0, "hello")], speaker="GROUCHO"),
        *make_cues([(2.1, 4.0, "hi")], speaker="CONTESTANT"),
    ]
    cues[1].index = 1
    atoms = build_atoms(cues)
    assert len(atoms) == 2
    assert atoms[0].speaker == "GROUCHO"


def test_atoms_break_when_too_long():
    cues = make_cues([(float(i) * 5, float(i) * 5 + 4.9, f"line {i}") for i in range(20)])
    atoms = build_atoms(cues, pause_gap=1.1, max_atom=30.0)
    assert all(a.duration <= 35.0 for a in atoms)
    assert len(atoms) > 1


def test_empty_cues_are_ignored():
    cues = make_cues([(0.0, 1.0, "  "), (1.0, 2.0, "real")])
    atoms = build_atoms(cues)
    assert len(atoms) == 1
    assert atoms[0].text == "real"


def test_grouping_respects_max_segment():
    cues = make_cues([(float(i) * 10, float(i) * 10 + 9.0, f"line {i}") for i in range(40)])
    atoms = build_atoms(cues, pause_gap=0.5)
    groups = group_atoms(atoms, min_segment=20, target_segment=55, max_segment=120)
    for group in groups:
        span = group[-1].end - group[0].start
        assert span <= 130, f"group spans {span}s"


def test_grouping_prefers_thought_openers():
    # Two candidate cut points at the same pause length; "So," should win.
    spans = [
        (0.0, 10.0, "first part of the story"),
        (10.6, 20.0, "still the same story"),
        (20.6, 30.0, "So here is a brand new thing"),
        (30.6, 40.0, "continuing the new thing"),
    ]
    atoms = build_atoms(make_cues(spans), pause_gap=0.5)
    groups = group_atoms(atoms, min_segment=15, target_segment=25, max_segment=120)
    starts = [g[0].text for g in groups]
    assert any(s.startswith("So here is") for s in starts)


def test_short_tail_folds_into_previous_group():
    spans = [(float(i) * 20, float(i) * 20 + 19.0, f"line {i}") for i in range(4)]
    spans.append((80.0, 82.0, "tiny tail"))
    atoms = build_atoms(make_cues(spans), pause_gap=0.5)
    groups = group_atoms(atoms, min_segment=20, target_segment=40, max_segment=120)
    assert groups[-1][-1].text == "tiny tail"
    assert (groups[-1][-1].end - groups[-1][0].start) >= 20


def test_split_source_produces_contiguous_non_overlapping_segments():
    # Realistic speech density (~2 words/sec); a sparser fixture would be
    # discarded by the non-speech filter.
    line = "and then he told me the funniest thing i had ever heard in my whole life"
    cues = make_cues([(float(i) * 8, float(i) * 8 + 7.5, f"{line} {i}") for i in range(30)])
    segments = split_source("ep01", cues)
    assert segments
    assert all(s.id.startswith("ep01:") for s in segments)
    for a, b in zip(segments, segments[1:], strict=False):
        assert a.end <= b.start, "segments must not overlap"
    assert all(s.text for s in segments)


def test_split_source_handles_empty_input():
    assert split_source("ep01", []) == []


def test_non_speech_artefacts_are_dropped():
    """Whisper emits a stock phrase over music/applause; 30s of "Thank you."
    is not content and must not reach the planner."""
    from conftest import make_segment

    from mashup.segment.splitter import drop_non_speech, speech_density

    junk = make_segment("junk", duration=30.0, text="Thank you.")
    real = make_segment("real", duration=30.0, text=" ".join(["word"] * 75))
    assert speech_density(junk) < 0.5
    assert speech_density(real) > 2.0
    assert [s.id for s in drop_non_speech([junk, real])] == ["real"]


def test_filter_keeps_sparse_but_genuine_speech():
    """A slow segment that still carries real dialogue must survive."""
    from conftest import make_segment

    from mashup.segment.splitter import drop_non_speech

    sparse = make_segment("sparse", duration=30.0, text=" ".join(["word"] * 20))
    assert [s.id for s in drop_non_speech([sparse])] == ["sparse"]


def test_segment_ids_are_stable_under_filtering():
    """Ordinals are assigned before filtering, so ids do not shift when the
    threshold is retuned."""
    cues = make_cues([(float(i) * 8, float(i) * 8 + 7.5, f"line {i} here now") for i in range(30)])
    kept = split_source("ep01", cues, filter_non_speech=True)
    unfiltered = split_source("ep01", cues, filter_non_speech=False)
    kept_ids = {s.id for s in kept}
    assert kept_ids <= {s.id for s in unfiltered}
