from __future__ import annotations

from mashup.models import Clip, Role, VisualInsert
from mashup.render.provenance import (
    label_text,
    source_timecode,
    write_label_card,
    write_visual_credit_card,
    write_watermark_card,
)


def clip(**updates) -> Clip:
    values = {
        "index": 0,
        "segment_id": "segment",
        "source_id": "episode-id",
        "source_title": "Building for People, Not Wallets",
        "source_path": "/tmp/episode.mp4",
        "start": 3671.4,
        "end": 3742.6,
        "render_start": 3671.4,
        "render_end": 3742.6,
        "text": "Material",
        "summary": "Material",
        "role": Role.SETUP,
        "energy": 0.5,
    }
    values.update(updates)
    return Clip(**values)


def test_source_timecode_supports_hour_long_podcasts():
    assert source_timecode(3671.4) == "01:01:11"


def test_label_uses_title_and_original_source_range():
    assert label_text(clip(), max_chars=80) == (
        "BUILDING FOR PEOPLE, NOT WALLETS | 01:01:11 - 01:02:23"
    )


def test_label_falls_back_to_source_id():
    text = label_text(clip(source_title=""), max_chars=80)
    assert text == "EPISODE-ID | 01:01:11 - 01:02:23"


def test_long_title_is_truncated_without_losing_timecode():
    text = label_text(clip(source_title="A" * 200), max_chars=40)
    assert text.startswith("A" * 39 + "-")
    assert text.endswith("01:01:11 - 01:02:23")


def test_card_is_a_deterministic_rgba_png(tmp_path):
    first = write_label_card(clip(), 1280, tmp_path)
    second = write_label_card(clip(), 1280, tmp_path)

    assert first == second
    raw = first.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert raw.endswith(b"IEND\xaeB`\x82")
    assert len(raw) > 500


def test_watermark_text_changes_its_deterministic_png(tmp_path):
    first = write_watermark_card("ZEROPOD", 1280, tmp_path)
    same = write_watermark_card("ZEROPOD", 1280, tmp_path)
    changed = write_watermark_card("ANOTHER SHOW", 1280, tmp_path)

    assert first == same
    assert first != changed
    assert first.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_visual_credit_is_a_deterministic_rgba_png(tmp_path):
    visual = VisualInsert(
        start=5,
        end=10,
        source_path="/tmp/archive.mp4",
        source_time=42,
        source_title="You Bet Your Life Collection",
        source_url="https://archive.org/details/ybylcollection",
    )

    first = write_visual_credit_card(visual, 1280, tmp_path)
    same = write_visual_credit_card(visual, 1280, tmp_path)

    assert first == same
    assert first.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
