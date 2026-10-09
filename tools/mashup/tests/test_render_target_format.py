from pathlib import Path
from types import SimpleNamespace

from mashup.render.cut import (
    DEFAULT_FPS,
    DEFAULT_SIZE,
    SOCIAL_SIZE,
    MediaInfo,
    _subtitle_filter,
    _target_format,
)


def test_social_subtitles_stay_inside_the_lower_safe_area() -> None:
    subtitle_filter = _subtitle_filter(Path("captions.srt"), "social")
    assert "FontSize=10" in subtitle_filter
    assert "MarginV=48" in subtitle_filter


def info(width: int, height: int, fps: float, *, video: bool = True) -> MediaInfo:
    return MediaInfo(
        has_video=video,
        has_audio=True,
        width=width,
        height=height,
        fps=fps,
        duration=10.0,
    )


def edl(*sources: str) -> SimpleNamespace:
    return SimpleNamespace(clips=[SimpleNamespace(source_path=source) for source in sources])


def test_target_format_uses_the_modal_source_format_instead_of_the_first_source():
    sources = edl("low.mp4", "wide-a.mp4", "wide-b.mp4")
    infos = {
        "low.mp4": info(640, 360, 24.0),
        "wide-a.mp4": info(1920, 1080, 30.0),
        "wide-b.mp4": info(1920, 1080, 30.0),
    }

    assert _target_format(sources, infos) == ((1920, 1080), 30.0)


def test_target_format_prefers_maximum_quality_when_every_source_format_is_unique():
    sources = edl("first.mp4", "largest.mp4", "fastest.mp4")
    infos = {
        "first.mp4": info(640, 360, 24.0),
        "largest.mp4": info(1920, 1080, 30.0),
        "fastest.mp4": info(1280, 720, 60.0),
    }

    assert _target_format(sources, infos) == ((1920, 1080), 30.0)


def test_target_format_counts_each_source_once_and_keeps_audio_only_default():
    repeated = edl("low.mp4", "low.mp4", "low.mp4", "high.mp4")
    infos = {
        "low.mp4": info(640, 360, 24.0),
        "high.mp4": info(1280, 720, 30.0),
    }
    audio_only = edl("voice.m4a")

    assert _target_format(repeated, infos) == ((1280, 720), 30.0)
    assert _target_format(audio_only, {"voice.m4a": info(0, 0, 30.0, video=False)}) == (
        DEFAULT_SIZE,
        DEFAULT_FPS,
    )


def test_social_profile_is_vertical_and_keeps_source_frame_rate():
    sources = edl("wide.mp4")
    infos = {"wide.mp4": info(1920, 1080, 24.0)}

    assert _target_format(sources, infos, "social") == (SOCIAL_SIZE, 24.0)
