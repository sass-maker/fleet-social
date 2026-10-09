from __future__ import annotations

from mashup.batch import build_batch, load_batch, save_batch, save_review_html
from mashup.models import EDL, Clip, Role, ScoreTerms, ShortReview


def edl(index: int) -> EDL:
    start = index * 100.0
    return EDL(
        strategy=f"short-{index:02d}",
        prompt="a concrete fundraising lesson",
        target_duration=45,
        generated_at="2026-08-27T00:00:00Z",
        clips=[
            Clip(
                index=0,
                segment_id=f"ep-{index}:{index}",
                source_id=f"ep-{index}",
                source_title=f"Founder Show {index}",
                source_path=f"/archive/ep-{index}.mp4",
                start=start,
                end=start + 45,
                render_start=start,
                render_end=start + 45,
                text=f"Founder lesson {index} with enough transcript to review.",
                summary="A complete founder lesson.",
                role=Role.DEVELOPMENT,
                energy=0.7,
            )
        ],
        score=0.4 - index * 0.005,
        terms=ScoreTerms(relevance=0.9 - index * 0.025, non_repetition=1, duration_fit=1),
        weights={"relevance": 0.2, "non_repetition": 0.12, "duration_fit": 0.1},
        short_review=ShortReview(
            can_open=True,
            can_end=True,
            hook_strength=0.72,
            payoff_strength=0.81,
            reason="A clear founder decision lands the clip.",
        ),
    )


def test_batch_manifest_round_trips_and_review_is_static(tmp_path):
    batch = build_batch(
        [edl(1), edl(2), edl(3)],
        collection="startups",
        collection_name="Startups",
        angle="fundraising",
        prompt="a concrete fundraising lesson",
        source_policy="Entitled sources only.",
        output_dir=tmp_path,
    )
    manifest = save_batch(batch, tmp_path / "batch.json")
    review = save_review_html(batch, tmp_path / "index.html")

    loaded = load_batch(manifest)
    assert loaded.schema_ == "fleet.mashup-clip-batch.v1"
    assert [item.edl_path for item in loaded.items] == [
        "clip-01.json",
        "clip-02.json",
        "clip-03.json",
    ]
    page = review.read_text()
    assert "Startups · fundraising" in page
    assert "localStorage" in page
    assert "Mashup does not publish" in page
    assert "Founder Show 1" in page
    assert "each signal shows value × weight" in page
    assert "Reviewed 0/3" in page
    assert "EDL JSON" in page
    assert loaded.items[0].weights["relevance"] == 0.2
    assert loaded.items[0].hook_strength == 0.72
    assert loaded.items[0].payoff_strength == 0.81
    assert "A clear founder decision lands the clip." in page


def test_batch_manifest_discovers_existing_render_artifacts(tmp_path):
    (tmp_path / "clip-01.mp4").write_bytes(b"video")
    (tmp_path / "clip-01.srt").write_text("captions")

    batch = build_batch(
        [edl(1), edl(2), edl(3)],
        collection="startups",
        collection_name="Startups",
        angle="fundraising",
        prompt="a concrete fundraising lesson",
        source_policy="Entitled sources only.",
        output_dir=tmp_path,
    )

    assert batch.items[0].video_path == "clip-01.mp4"
    assert batch.items[0].captions_path == "clip-01.srt"
    assert batch.items[1].video_path is None
