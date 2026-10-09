"""Pure unit tests for cut-point snapping and the silencedetect parser.

Nothing here shells out to ffmpeg.
"""

from __future__ import annotations

import pytest

from mashup.models import Cue
from mashup.render.boundaries import MIN_CLIP_DURATION, parse_silencedetect, snap_boundaries

# Captured from `ffmpeg -af silencedetect=noise=-32dB:d=0.28 -f null -`.
SAMPLE_STDERR = """\
[Parsed_silencedetect_0 @ 0x600003a04000] silence_start: 3.11875
[Parsed_silencedetect_0 @ 0x600003a04000] silence_end: 4.02292 | silence_duration: 0.904167
size=N/A time=00:00:05.00 bitrate=N/A speed=  42x
[Parsed_silencedetect_0 @ 0x600003a04000] silence_start: 11.2
[Parsed_silencedetect_0 @ 0x600003a04000] silence_end: 12.75 | silence_duration: 1.55
[out#0/null @ 0x13be05f30] video:0KiB audio:0KiB subtitle:0KiB
"""


def cues(*spans: tuple[float, float]) -> list[Cue]:
    return [
        Cue(index=i, start=s, end=e, text=f"line {i}") for i, (s, e) in enumerate(spans, start=1)
    ]


class TestParseSilencedetect:
    def test_pairs_starts_and_ends(self):
        assert parse_silencedetect(SAMPLE_STDERR) == [(3.11875, 4.02292), (11.2, 12.75)]

    def test_ignores_unpaired_trailing_start(self):
        # A file that fades out into silence never logs the closing end.
        text = SAMPLE_STDERR + "[silencedetect @ 0x1] silence_start: 30.5\n"
        assert parse_silencedetect(text) == [(3.11875, 4.02292), (11.2, 12.75)]

    def test_ignores_duration_field_and_noise(self):
        assert parse_silencedetect("frame= 120 fps=30 silence_duration: 9.9\n") == []

    def test_empty_stderr(self):
        assert parse_silencedetect("") == []

    def test_clamps_negative_start(self):
        text = "silence_start: -0.004\nsilence_end: 1.5 | silence_duration: 1.5\n"
        assert parse_silencedetect(text) == [(0.0, 1.5)]


class TestSnapBoundaries:
    def test_snaps_to_silence_midpoint(self):
        start, end = snap_boundaries(10.0, 20.0, silences=[(9.4, 9.8), (20.2, 20.8)])
        assert start == pytest.approx(9.6)
        assert end == pytest.approx(20.5)

    def test_prefers_cue_gap_when_no_silence_is_near(self):
        # Gap between cue 1 (ends 9.4) and cue 2 (starts 10.0) -> midpoint 9.7.
        start, _ = snap_boundaries(
            10.0, 20.0, silences=[(40.0, 41.0)], cues=cues((5.0, 9.4), (10.0, 14.0))
        )
        assert start == pytest.approx(9.7)

    def test_silence_wins_over_cue_gap(self):
        start, _ = snap_boundaries(
            10.0,
            20.0,
            silences=[(9.4, 9.8)],
            cues=cues((5.0, 9.0), (9.9, 14.0)),
        )
        assert start == pytest.approx(9.6)

    def test_respects_window(self):
        # The only silence sits 3s away; outside the 1.2s window it is ignored.
        assert snap_boundaries(10.0, 20.0, silences=[(6.8, 7.2)]) == (10.0, 20.0)

    def test_window_is_configurable(self):
        start, _ = snap_boundaries(10.0, 20.0, silences=[(6.8, 7.2)], window=4.0)
        assert start == pytest.approx(7.0)

    def test_expands_outward_not_inward(self):
        # Equidistant candidates on both sides: the start takes the earlier one
        # and the end the later one, so no speech is clipped.
        silences = [(8.5, 8.7), (11.3, 11.5), (18.5, 18.7), (21.3, 21.5)]
        start, end = snap_boundaries(10.0, 20.0, silences=silences, window=1.6)
        assert start == pytest.approx(8.6)
        assert end == pytest.approx(21.4)

    def test_inward_snap_only_when_nothing_outward(self):
        start, _ = snap_boundaries(10.0, 20.0, silences=[(10.4, 10.6)])
        assert start == pytest.approx(10.5)

    def test_never_inverts(self):
        # One silence sits between the two targets and attracts both of them.
        start, end = snap_boundaries(10.0, 11.0, silences=[(10.8, 11.4)])
        assert start < end
        assert end - start >= MIN_CLIP_DURATION
        # Falling back widens rather than narrows the original span.
        assert start <= 10.0
        assert end >= 11.0

    def test_pads_a_too_short_planner_span(self):
        start, end = snap_boundaries(10.0, 10.2)
        assert start == pytest.approx(10.0)
        assert end == pytest.approx(10.0 + MIN_CLIP_DURATION)

    def test_never_negative(self):
        start, end = snap_boundaries(0.3, 5.0, silences=[(0.0, 0.2)])
        assert start >= 0.0
        assert end == pytest.approx(5.0)

    def test_no_hints_is_identity(self):
        assert snap_boundaries(12.5, 30.25) == (12.5, 30.25)

    def test_ignores_zero_length_cue_gaps(self):
        # Back-to-back cues have no pause to cut in, so nothing to snap to.
        assert snap_boundaries(10.0, 20.0, cues=cues((5.0, 10.4), (10.4, 14.0))) == (10.0, 20.0)
