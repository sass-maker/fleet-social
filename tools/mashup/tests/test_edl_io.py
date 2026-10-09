"""EDL round-trip and review-transcript formatting."""

from __future__ import annotations

import json

from mashup.models import EDL, Clip, Role, ScoreTerms
from mashup.render.edl_io import edl_to_transcript, load_edl, save_edl


def make_edl() -> EDL:
    return EDL(
        strategy="escalation",
        prompt="bits about airports",
        target_duration=60.0,
        generated_at="2026-07-25T10:00:00Z",
        clips=[
            Clip(
                index=1,
                segment_id="seg-a",
                source_id="ep-01",
                source_path="/archive/ep01.mp4",
                start=5.5,
                end=20.0,
                render_start=5.0,
                render_end=20.0,
                text="The security line is the only queue where everyone "
                "voluntarily takes their shoes off for a stranger.",
                summary="airport security bit",
                role=Role.SETUP,
                energy=0.4,
                topics=["airports", "security"],
            ),
            Clip(
                index=2,
                segment_id="seg-b",
                source_id="ep-07",
                source_path="/archive/ep07.m4a",
                start=610.0,
                end=634.0,
                render_start=609.5,
                render_end=634.5,
                text="And then they page you by name.",
                summary="boarding callback",
                role=Role.CALLBACK,
                energy=0.85,
                transition="crossfade",
                edited=True,
            ),
        ],
        score=0.72,
        terms=ScoreTerms(relevance=0.9, callback=0.5),
        weights={"relevance": 1.0, "callback": 0.4},
        rationale=["ep-07 callback lands after its ep-01 referent"],
    )


class TestRoundTrip:
    def test_load_returns_an_equal_edl(self, tmp_path):
        edl = make_edl()
        path = tmp_path / "nested" / "edl.json"
        save_edl(edl, path)
        assert load_edl(path) == edl

    def test_file_is_indented_and_key_sorted(self, tmp_path):
        path = tmp_path / "edl.json"
        save_edl(make_edl(), path)
        raw = path.read_text()
        assert raw.endswith("\n")
        assert '\n  "clips": [' in raw  # indent=2
        keys = list(json.loads(raw).keys())
        assert keys == sorted(keys)

    def test_derived_fields_survive(self, tmp_path):
        path = tmp_path / "edl.json"
        save_edl(make_edl(), path)
        reloaded = load_edl(path)
        assert reloaded.duration == make_edl().duration
        assert reloaded.clips[1].role is Role.CALLBACK
        assert reloaded.clips[1].edited is True


class TestTranscript:
    def test_header(self):
        lines = edl_to_transcript(make_edl()).splitlines()
        assert lines[0] == 'escalation: "bits about airports"'
        # 15s + 25s rendered against a 60s target.
        assert lines[1] == "2 clips, 00:40 (target 01:00)"

    def test_clip_locator_lines(self):
        text = edl_to_transcript(make_edl())
        assert "[01] ep-01 @ 00:05-00:20 (15s, setup, 0.40)" in text
        assert "[02] ep-07 @ 10:10-10:34 (25s, callback, 0.85)" in text

    def test_text_is_wrapped_and_indented(self):
        text = edl_to_transcript(make_edl())
        body = [ln for ln in text.splitlines() if ln.startswith("    ")]
        assert body, "clip text should be indented under its locator line"
        assert all(len(ln) <= 88 for ln in body)
        assert body[0].startswith("    The security line")

    def test_ends_with_newline(self):
        assert edl_to_transcript(make_edl()).endswith("\n")
