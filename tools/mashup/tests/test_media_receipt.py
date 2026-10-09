from __future__ import annotations

from mashup.media_receipt import MEDIA_RECEIPT_SCHEMA, build_media_receipt


def _edit() -> dict:
    return {
        "schema": "fleet.podcast-edit.v1",
        "id": "owned-episode-short",
        "approval": {"status": "approved", "approvedBy": "operator"},
        "sources": [
            {
                "id": "episode-1",
                "title": "Owned episode",
                "sourceUrl": "https://example.com/episode-1",
                "license": "creator-owned",
                "licenseUrl": "https://example.com/rights",
                "sha256": "a" * 64,
            }
        ],
    }


def test_receipt_is_self_describing_and_hashes_artifacts(tmp_path) -> None:
    video = tmp_path / "result.mp4"
    captions = tmp_path / "result.srt"
    video.write_bytes(b"fixture-video-bytes")
    captions.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")

    receipt = build_media_receipt(
        _edit(),
        video_path=video,
        captions_path=captions,
        duration_seconds=30,
        width=1080,
        height=1920,
        model_revisions={"transcription": "whisper-small@local"},
        generated_at="2026-08-09T00:00:00+00:00",
    )

    assert receipt["schema"] == MEDIA_RECEIPT_SCHEMA
    assert receipt["output"]["video"]["sha256"]
    assert receipt["output"]["captions"]["sha256"]
    assert receipt["sources"][0]["license"] == "creator-owned"
    assert receipt["validation"] == {
        "artifactHashVerified": True,
        "approvalVerified": True,
        "provenanceVerified": True,
    }


def test_receipt_rejects_unapproved_or_incomplete_input(tmp_path) -> None:
    video = tmp_path / "result.mp4"
    video.write_bytes(b"fixture-video-bytes")
    edit = _edit()
    edit["approval"] = {"status": "proposed"}

    try:
        build_media_receipt(edit, video_path=video, duration_seconds=30, width=1080, height=1920)
    except ValueError as error:
        assert "approved" in str(error)
    else:
        raise AssertionError("unapproved edit should fail")
