"""Subtitle parser tests.

Fixtures are inline strings written to tmp_path: these must pass on a machine
with no ffmpeg, no models, and no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mashup.ingest.subtitles import SubtitleError, parse_subtitles, parse_timestamp_map


def write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


BASIC_SRT = """\
1
00:00:01,000 --> 00:00:03,500
Hello there.

2
00:00:04,000 --> 00:00:06,250
This is
a two line cue.
"""


def test_basic_srt(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "a.srt", BASIC_SRT))
    assert [c.index for c in cues] == [0, 1]
    assert cues[0].start == 1.0
    assert cues[0].end == 3.5
    assert cues[0].text == "Hello there."
    assert cues[0].duration == pytest.approx(2.5)
    # Intra-cue line breaks are layout, not meaning.
    assert cues[1].text == "This is a two line cue."


def test_srt_without_index_lines(tmp_path: Path) -> None:
    body = "00:00:00,000 --> 00:00:02,000\nNo index here.\n"
    cues = parse_subtitles(write(tmp_path, "b.srt", body))
    assert [c.text for c in cues] == ["No index here."]


def test_srt_crlf_and_bom(tmp_path: Path) -> None:
    path = tmp_path / "c.srt"
    path.write_text("1\r\n00:00:01,000 --> 00:00:02,000\r\nWindows.\r\n", encoding="utf-8-sig")
    cues = parse_subtitles(path)
    assert [c.text for c in cues] == ["Windows."]


MARKUP_SRT = """\
1
00:00:01,000 --> 00:00:02,000
{\\an8}<i>Italic</i> and <b>bold</b>.

2
00:00:03,000 --> 00:00:04,000
<font color="#ffff00">Yellow</font> text &amp; entities.
"""


def test_srt_markup_is_stripped(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "m.srt", MARKUP_SRT))
    assert [c.text for c in cues] == ["Italic and bold.", "Yellow text & entities."]


VTT = """\
WEBVTT
X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000

NOTE this is a comment block
that spans two lines

STYLE
::cue { color: yellow }

REGION
id:speaker

intro-cue
00:00:01.000 --> 00:00:02.000 align:start position:10% line:0 size:50%
<v Roger Bannister>Hello from a voice span.

00:00:03.000 --> 00:00:04.000
<c.loud>Shouted</c> <00:00:03.500>words here.
"""


def test_vtt_notes_settings_and_voices(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "a.vtt", VTT))
    assert len(cues) == 2, "NOTE/STYLE/REGION blocks must not become cues"

    assert cues[0].start == 1.0
    assert cues[0].speaker == "Roger Bannister"
    # Cue settings are stripped, not appended to the text.
    assert cues[0].text == "Hello from a voice span."

    assert cues[1].speaker is None
    assert cues[1].text == "Shouted words here."


YOUTUBE_ROLLING_VTT = """\
WEBVTT
Kind: captions

00:00:01.000 --> 00:00:03.000 align:start position:0%
Welcome<00:00:01.200><c> to</c><00:00:01.400><c> the</c><00:00:01.600><c> show.</c>

00:00:03.000 --> 00:00:03.010 align:start position:0%
Welcome to the show.

00:00:03.010 --> 00:00:05.000 align:start position:0%
Welcome to the show.
Thanks<00:00:03.200><c> for</c><00:00:03.400><c> having</c><00:00:03.600><c> me.</c>

00:00:05.000 --> 00:00:05.010 align:start position:0%
Thanks for having me.

00:00:05.010 --> 00:00:07.000 align:start position:0%
Thanks for having me.
Let's<00:00:05.200><c> begin.</c>
"""


def test_youtube_rolling_vtt_emits_each_phrase_once(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "youtube.vtt", YOUTUBE_ROLLING_VTT))
    assert [cue.text for cue in cues] == [
        "Welcome to the show.",
        "Thanks for having me.",
        "Let's begin.",
    ]
    assert [cue.index for cue in cues] == [0, 1, 2]


def test_vtt_timestamp_map_parses_but_does_not_offset(tmp_path: Path) -> None:
    fields = parse_timestamp_map("WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000")
    assert fields["MPEGTS"] == "900000"
    # The parsed offset is deliberately not applied to cue times.
    cues = parse_subtitles(write(tmp_path, "b.vtt", VTT))
    assert cues[0].start == 1.0


MISSING_HOURS = """\
WEBVTT

01:05.500 --> 01:07.000
Short form timestamps.
"""


def test_missing_hours_field(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "h.vtt", MISSING_HOURS))
    assert cues[0].start == pytest.approx(65.5)
    assert cues[0].end == pytest.approx(67.0)


def test_missing_hours_in_srt(tmp_path: Path) -> None:
    body = "1\n00:10,000 --> 00:12,000\nTen seconds in.\n"
    cues = parse_subtitles(write(tmp_path, "h.srt", body))
    assert cues[0].start == 10.0


MALFORMED_SRT = """\
1
00:00:01,000 --> 00:00:02,000
Good one.

2
this is not a timing line
Orphaned text.

3
00:00:XX,000 --> 00:00:09,000
Bad digits.

4
00:00:10,000 --> 00:00:08,000
Ends before it starts.

5
00:00:11,000 --> 00:00:12,000
Good two.
"""


def test_malformed_blocks_are_skipped_not_fatal(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "bad.srt", MALFORMED_SRT))
    assert [c.text for c in cues] == ["Good one.", "Good two."]
    # Surviving cues are renumbered densely.
    assert [c.index for c in cues] == [0, 1]


SPEAKERS_SRT = """\
1
00:00:01,000 --> 00:00:02,000
JOHN: Where were you?

2
00:00:02,000 --> 00:00:03,000
Dr. Ana: In the lab.

3
00:00:03,000 --> 00:00:04,000
SPEAKER 2: Over here.

4
00:00:04,000 --> 00:00:05,000
<i>MARIA:</i> With markup around the tag.

5
00:00:05,000 --> 00:00:06,000
I told him: the whole thing was a bit, honestly.

6
00:00:06,000 --> 00:00:07,000
Go to https://example.com now.
"""


def test_speaker_extraction(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "s.srt", SPEAKERS_SRT))
    assert [(c.speaker, c.text) for c in cues] == [
        ("JOHN", "Where were you?"),
        ("Dr. Ana", "In the lab."),
        ("SPEAKER 2", "Over here."),
        ("MARIA", "With markup around the tag."),
        # A mid-sentence colon is punctuation, not a label.
        (None, "I told him: the whole thing was a bit, honestly."),
        (None, "Go to https://example.com now."),
    ]


EMPTY_CUES_SRT = """\
1
00:00:01,000 --> 00:00:02,000
<i></i>

2
00:00:02,000 --> 00:00:03,000
{\\an8}

3
00:00:03,000 --> 00:00:04,000
Real content.
"""


def test_empty_cues_dropped_and_reindexed(tmp_path: Path) -> None:
    cues = parse_subtitles(write(tmp_path, "e.srt", EMPTY_CUES_SRT))
    assert len(cues) == 1
    assert cues[0].index == 0
    assert cues[0].text == "Real content."


def test_empty_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SubtitleError):
        parse_subtitles(write(tmp_path, "empty.srt", "\n\n"))


def test_header_only_vtt_raises(tmp_path: Path) -> None:
    with pytest.raises(SubtitleError):
        parse_subtitles(write(tmp_path, "empty.vtt", "WEBVTT\n\nNOTE nothing to see\n"))


def test_unsupported_suffix_raises(tmp_path: Path) -> None:
    with pytest.raises(SubtitleError):
        parse_subtitles(write(tmp_path, "subs.ass", "whatever"))


def test_whisper_special_tokens_are_stripped(tmp_path: Path) -> None:
    """WhisperKit writes raw whisper tokens into its SRT output.

    These are not markup, so the generic tag regex skips them. Left in, they
    reach segment text, embeddings and the enrichment prompt.
    """
    content = (
        "1\n"
        "00:00:00,000 --> 00:00:04,000\n"
        "<|startoftranscript|><|en|><|transcribe|><|0.00|> "
        "I play the part of Peter Minuet.<|4.00|>\n\n"
        "2\n"
        "00:00:04,000 --> 00:00:07,000\n"
        "<|4.00|> And if you rush to your nearest theater.<|7.00|>\n"
    )
    cues = parse_subtitles(write(tmp_path, "wk.srt", content))
    assert [c.text for c in cues] == [
        "I play the part of Peter Minuet.",
        "And if you rush to your nearest theater.",
    ]
    assert not any("<|" in c.text for c in cues)
