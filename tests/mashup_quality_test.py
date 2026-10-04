import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from mashup_quality import choose_excerpt, layout_scene, scene_duration, text_width, write_captions, write_soundtrack


class MashupQualityTests(unittest.TestCase):
    def test_long_and_literal_text_remains_inside_video_safe_area(self):
        for text in ["W" * 180, "100% clearer: use {real} examples, not placeholders.", "A clear story. " * 12]:
            size, lines = layout_scene(text)
            self.assertEqual("".join("".join(lines).split()), "".join(text.split()))
            self.assertTrue(all(text_width(line, size) <= 850 for line in lines))
            self.assertLessEqual(len(lines) * size + (len(lines) - 1) * 22, 840)

    def test_reading_time_grows_with_copy_and_stays_bounded(self):
        self.assertGreater(scene_duration("A clear product story with enough words to read comfortably."), scene_duration("Try it."))
        self.assertLessEqual(scene_duration("word " * 100), 8)

    def test_captionless_fallback_is_bounded_and_never_invents_captions(self):
        self.assertEqual(choose_excerpt([], "product", 90), (0.0, 10.0, []))
        self.assertEqual(choose_excerpt([], "product", 3), (0.0, 3, []))

    def test_relevant_later_excerpt_preserves_sentence_boundaries(self):
        cues = [
            {"start": 0, "end": 5, "text": "Welcome to today's conversation."},
            {"start": 5, "end": 10, "text": "First we will discuss the weather."},
            {"start": 20, "end": 24, "text": "Products need people to scale them"},
            {"start": 24, "end": 28, "text": "and maintain them."},
        ]
        start, end, selected = choose_excerpt(cues, "People who scale and maintain products", 30)
        self.assertEqual((start, end), (20, 28))
        self.assertTrue(selected[-1]["text"].endswith("."))

    def test_repeated_spoken_excerpt_is_penalized(self):
        cues = [
            {"start": 0, "end": 5, "text": "Products need people to scale and maintain them."},
            {"start": 10, "end": 15, "text": "Technology creates new opportunities for everyone."},
        ]
        start, end, _ = choose_excerpt(cues, "Products scale maintain technology opportunities", 20, ["Products need people to scale and maintain them."], limit=6)
        self.assertEqual((start, end), (10, 15))

    def test_many_topic_words_cannot_outscore_an_unfinished_sentence(self):
        cues = [
            {"start": 0, "end": 5, "text": "Products need builders to maintain useful things."},
            {"start": 5, "end": 10, "text": "Ideas become progress when people build"},
            {"start": 10, "end": 16, "text": "and maintain the useful things they have created."},
        ]
        start, end, selected = choose_excerpt(cues, "Ideas become progress when people build and maintain useful products", 20, limit=10)
        self.assertEqual((start, end), (0, 5))
        self.assertEqual(len(selected), 1)

    def test_excerpt_does_not_start_halfway_through_a_sentence(self):
        cues = [
            {"start": 0, "end": 6, "text": "A long introduction explains why"},
            {"start": 6, "end": 12, "text": "builders maintain products."},
            {"start": 12, "end": 18, "text": "Products require maintenance."},
        ]
        start, end, _ = choose_excerpt(cues, "Builders maintain products", 20, limit=8)
        self.assertEqual((start, end), (12, 18))

    def test_missing_relevance_or_sentence_boundaries_requests_a_new_source(self):
        for cues in [
            [{"start": 0, "end": 6, "text": "An athlete overcame a sports injury."}],
            [{"start": 0, "end": 6, "text": "Products and builders need"}],
            [{"start": 0, "end": 6, "text": "Products are built in the U.S."}, {"start": 6, "end": 20, "text": "and elsewhere around the world."}],
        ]:
            with self.subTest(cues=cues), self.assertRaisesRegex(ValueError, "complete.*brief"):
                choose_excerpt(cues, "Build and maintain products", 30, limit=14)

    def test_long_caption_gap_cannot_join_two_partial_sentences(self):
        cues = [
            {"start": 0, "end": 3, "text": "Products need"},
            {"start": 8, "end": 11, "text": "builders."},
            {"start": 11, "end": 14, "text": "Builders maintain useful products."},
        ]
        start, end, _ = choose_excerpt(cues, "Builders maintain useful products", 20)
        self.assertEqual((start, end), (11, 14))

    def test_specific_dense_passage_beats_many_generic_words(self):
        cues = [
            {"start": 0, "end": 6, "text": "People need new things and ideas to make progress."},
            {"start": 6, "end": 12, "text": "Maintain useful products as technology changes."},
        ]
        start, end, _ = choose_excerpt(cues, "People need new things: maintain useful products as technology changes", 20, limit=7)
        self.assertEqual((start, end), (6, 12))

    def test_complete_sentence_beyond_media_end_is_not_truncated(self):
        with self.assertRaisesRegex(ValueError, "complete.*brief"):
            choose_excerpt([{"start": 0, "end": 9, "text": "Builders maintain products."}], "Builders maintain products", 6)

    def test_quotes_close_sentences_but_ellipsis_does_not(self):
        cues = [
            {"start": 0, "end": 3, "text": "We build products…"},
            {"start": 3, "end": 6, "text": "and keep working on them."},
            {"start": 6, "end": 10, "text": "She said, ‘Builders maintain products.’"},
        ]
        start, end, _ = choose_excerpt(cues, "Builders maintain products", 12, limit=4)
        self.assertEqual((start, end), (6, 10))

    def test_repeated_passage_cannot_hide_inside_a_longer_excerpt(self):
        cues = [
            {"start": 0, "end": 5, "text": "Products need builders to maintain them."},
            {"start": 5, "end": 10, "text": "Technology changes companies every year."},
        ]
        with self.assertRaisesRegex(ValueError, "distinct complete.*brief"):
            choose_excerpt(cues, "Builders maintain products", 20, ["Products need builders to maintain them."])

    def test_caption_times_retain_millisecond_carry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "captions.srt"
            write_captions(path, [{"start": 59.9996, "end": 63, "text": "Actual spoken words."}])
            self.assertIn("00:01:00,000 --> 00:01:03,000", path.read_text())

    def test_original_soundtrack_is_audible_quiet_and_has_exact_length(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track.wav"
            write_soundtrack(path, 4.5)
            with wave.open(str(path)) as audio:
                self.assertEqual(audio.getnframes(), 216000)
                samples = struct.unpack("<" + "h" * (audio.getnframes() * 2), audio.readframes(audio.getnframes()))
                self.assertGreater(max(samples), 500)
                self.assertLessEqual(max(abs(sample) for sample in samples), 2458)
                self.assertEqual(samples[:2], (0, 0))


if __name__ == "__main__":
    unittest.main()
