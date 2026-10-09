from __future__ import annotations

import json

import pytest

from mashup.models import EDL, Clip, Role, ScoreTerms
from mashup.podcast_contract import export_podcast_edit, save_podcast_edit


def _edl() -> EDL:
    return EDL(
        strategy="short",
        prompt="conviction over money",
        target_duration=45,
        generated_at="2026-07-29T07:06:22+00:00",
        clips=[
            Clip(
                index=0,
                segment_id="episode-1:0089",
                segment_ids=["episode-1:0089"],
                source_id="episode-1",
                source_title="Episode 1",
                source_path="/owned/episode-1.mp3",
                start=3396.238,
                end=3442.498,
                render_start=3396.238,
                render_end=3442.498,
                text="Find something that you believe in.",
                summary="Advice about conviction.",
                role=Role.CLOSER,
                energy=0.9,
                topics=["conviction"],
            )
        ],
        score=0.75,
        terms=ScoreTerms(
            relevance=0.66,
            context_completeness=1,
            non_repetition=1,
            progression=1,
            escalation=0.5,
            callback=0,
            duration_fit=0.97,
            source_diversity=0,
        ),
        weights={
            "relevance": 0.2,
            "context_completeness": 0.18,
            "non_repetition": 0.12,
            "progression": 0.12,
            "escalation": 0.2,
            "callback": 0.04,
            "duration_fit": 0.1,
            "source_diversity": 0.04,
        },
        calibration={"source": "corpus:135"},
        rationale=["one contiguous source-faithful window"],
    )


def _provenance(tmp_path):
    path = tmp_path / "PROVENANCE.json"
    path.write_text(
        json.dumps(
            {
                "creator": "Owner",
                "feed": "https://example.com/feed.xml",
                "license": "CC0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "files": [
                    {
                        "filename": "episode-1.mp3",
                        "source_url": "https://example.com/episode-1.mp3",
                        "sha256": "a" * 64,
                    }
                ],
            }
        )
    )
    return path


def test_export_preserves_the_complete_edl_and_rights(tmp_path) -> None:
    edl = _edl()
    payload = export_podcast_edit(
        edl,
        edit_id="conviction-short",
        provenance_path=_provenance(tmp_path),
        approval_status="approved",
        approved_by="operator",
        watermark_text="ZEROPOD",
    )

    assert payload["schema"] == "fleet.podcast-edit.v1"
    assert payload["editorial"] == edl.model_dump(mode="json")
    assert set(payload["editorial"]["terms"]) == {
        "relevance",
        "context_completeness",
        "non_repetition",
        "progression",
        "escalation",
        "callback",
        "duration_fit",
        "source_diversity",
    }
    assert payload["sources"][0]["sourceUrl"] == "https://example.com/episode-1.mp3"
    assert payload["sources"][0]["license"] == "CC0"
    assert payload["presentation"]["watermarkText"] == "ZEROPOD"


def test_approved_export_requires_an_approver(tmp_path) -> None:
    with pytest.raises(ValueError, match="approved_by"):
        export_podcast_edit(
            _edl(),
            edit_id="conviction-short",
            provenance_path=_provenance(tmp_path),
            approval_status="approved",
        )


def test_every_edl_source_must_have_provenance(tmp_path) -> None:
    provenance = _provenance(tmp_path)
    payload = json.loads(provenance.read_text())
    payload["files"] = []
    provenance.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="no file entry"):
        export_podcast_edit(
            _edl(),
            edit_id="conviction-short",
            provenance_path=provenance,
        )


def test_saved_contract_is_stable_and_readable(tmp_path) -> None:
    payload = export_podcast_edit(
        _edl(),
        edit_id="conviction-short",
        provenance_path=_provenance(tmp_path),
    )
    destination = tmp_path / "nested" / "podcast-edit.json"
    save_podcast_edit(payload, destination)
    assert json.loads(destination.read_text()) == payload
    assert destination.read_text().endswith("\n")


def test_export_rejects_repeated_material_and_source_audio(tmp_path) -> None:
    edl = _edl()
    first = edl.clips[0]
    repeated = first.model_copy(
        update={
            "index": 1,
            "start": 3500,
            "end": 3520,
            "render_start": 3500,
            "render_end": 3520,
        }
    )
    with pytest.raises(ValueError, match="repeat material id"):
        export_podcast_edit(
            edl.model_copy(update={"clips": [first, repeated]}),
            edit_id="repeated-material",
            provenance_path=_provenance(tmp_path),
        )

    overlap = first.model_copy(
        update={
            "index": 1,
            "segment_id": "episode-1:0090",
            "segment_ids": ["episode-1:0090"],
            "start": 3440,
            "end": 3460,
            "render_start": 3440,
            "render_end": 3460,
        }
    )
    with pytest.raises(ValueError, match="overlapping planned audio"):
        export_podcast_edit(
            edl.model_copy(update={"clips": [first, overlap]}),
            edit_id="overlapping-audio",
            provenance_path=_provenance(tmp_path),
        )


def test_export_preserves_a_long_form_multi_clip_timeline(tmp_path) -> None:
    edl = _edl()
    template = edl.clips[0]
    clips = [
        template.model_copy(
            update={
                "index": 0,
                "segment_id": "episode-1:long-1",
                "segment_ids": ["episode-1:long-1"],
                "start": 0,
                "end": 210,
                "render_start": 0,
                "render_end": 210,
            }
        ),
        template.model_copy(
            update={
                "index": 1,
                "segment_id": "episode-1:long-2",
                "segment_ids": ["episode-1:long-2"],
                "start": 210,
                "end": 420,
                "render_start": 210,
                "render_end": 420,
            }
        ),
    ]
    long_form = edl.model_copy(
        update={"strategy": "escalation", "target_duration": 420, "clips": clips}
    )

    payload = export_podcast_edit(
        long_form,
        edit_id="seven-minute-edit",
        provenance_path=_provenance(tmp_path),
    )

    assert payload["editorial"]["target_duration"] == 420
    assert (
        sum(clip["render_end"] - clip["render_start"] for clip in payload["editorial"]["clips"])
        == 420
    )
