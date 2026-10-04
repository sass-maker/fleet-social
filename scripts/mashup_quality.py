"""Small, local quality controls for Fleet Social's Mashup source preparation."""

import ctypes
import math
import re
import struct
import sys
import textwrap
import wave
from collections import Counter
from functools import lru_cache
from pathlib import Path

STOP_WORDS = set("a an and are as at be by can for from have i in is it of on or our that the their them then this to we what with you your again also become could do does everyone had has how just like make many more most need new no not only other people really repeat say see some think thing things very was were when which who will would".split())


@lru_cache(maxsize=512)
def text_width(text, size=66):
    if sys.platform != "darwin":
        return len(text) * size
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ct.CTFontCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_void_p]
    ct.CTFontCreateWithName.restype = ctypes.c_void_p
    ct.CTFontGetGlyphsForCharacters.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    ct.CTFontGetAdvancesForGlyphs.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
    ct.CTFontGetAdvancesForGlyphs.restype = ctypes.c_double
    name = cf.CFStringCreateWithCString(None, b"Arial-BoldMT", 0x08000100)
    font = ct.CTFontCreateWithName(name, size, None)
    raw = text.encode("utf-16-le")
    count = len(raw) // 2
    characters = (ctypes.c_uint16 * count).from_buffer_copy(raw)
    glyphs = (ctypes.c_uint16 * count)()
    try:
        ct.CTFontGetGlyphsForCharacters(font, characters, glyphs, count)
        return ct.CTFontGetAdvancesForGlyphs(font, 0, glyphs, None, count)
    finally:
        cf.CFRelease(font)
        cf.CFRelease(name)


def wrap_scene(text, width=850, size=66):
    words = text.split()
    lines = []
    line = ""
    for word in words:
        candidate = (line + " " + word).strip()
        if text_width(candidate, size) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ""
        for character in word:
            if line and text_width(line + character, size) > width:
                lines.append(line)
                line = ""
            line += character
    if line:
        lines.append(line)
    return lines


def scene_duration(text):
    return round(min(8, max(4, len(text.split()) / 2.8 + 1.2)), 2)


def layout_scene(text):
    for size in range(66, 39, -2):
        lines = wrap_scene(text, size=size)
        if len(lines) * size + max(0, len(lines) - 1) * 22 <= 840:
            return size, lines
    raise ValueError('Shorten this scene so its text fits the video safe area.')


def keywords(text):
    terms = set()
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in STOP_WORDS or len(word) <= 2:
            continue
        if word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("s") and not word.endswith(("ss", "us")) and len(word) > 3:
            word = word[:-1]
        if word == "built":
            word = "build"
        elif word.endswith("ing") and len(word) > 5:
            word = word[:-3]
        elif word.endswith("ed") and len(word) > 4:
            word = word[:-2]
        if len(word) > 4 and word[-1] == word[-2] and word[-1] in "bdgmnprt":
            word = word[:-1]
        if word.endswith("er") and len(word) > 6:
            word = word[:-2]
        if word.endswith("e") and len(word) > 4:
            word = word[:-1]
        terms.add(word)
    return terms


def ends_sentence(text):
    text = text.strip().rstrip('"\'”’)]')
    if text.endswith(("...", "…")) or re.search(r"\b(?:Mr|Mrs|Ms|Dr|Prof|vs|etc|e\.g|i\.e|U\.S)\.$", text, re.IGNORECASE):
        return False
    return bool(re.search(r"[.!?]$", text))


def choose_excerpt(cues, brief, duration, previous=(), limit=24):
    if not cues:
        return 0.0, min(10.0, duration), []
    target = keywords(brief)
    frequencies = Counter(term for cue in cues for term in keywords(cue["text"]))
    best = None
    previous_words = [keywords(text) for text in previous]
    previous_passages = [re.sub(r"\W+", " ", text.lower()).strip() for text in previous if len(text.split()) >= 4]
    for index, cue in enumerate(cues):
        if index and not ends_sentence(cues[index - 1]["text"]):
            continue
        start = max(0.0, cue["start"])
        window = []
        for following in cues[index:]:
            end = following["end"]
            if end > duration or end - start > limit or window and following["start"] - window[-1]["end"] > 1.5:
                break
            if end <= start:
                continue
            window.append(following)
            if end - start < min(2, duration) or not ends_sentence(following["text"]):
                continue
            text = " ".join(item["text"] for item in window)
            normalized = re.sub(r"\W+", " ", text.lower()).strip()
            if any(passage in normalized for passage in previous_passages):
                continue
            terms = keywords(text)
            matched = target & terms
            if not matched:
                continue
            overlap = max((len(terms & words) / max(1, len(terms | words)) for words in previous_words), default=0)
            specificity = sum(math.log1p(len(cues) / (1 + frequencies[term])) for term in matched)
            score = specificity / math.sqrt(max(1, len(terms))) * (1 + len(matched) / max(1, len(target))) - overlap * 4
            if best is None or score > best[0]:
                best = (score, start, end, list(window))
    if best:
        return best[1:]
    raise ValueError(f"No distinct complete passage matching your brief fits within {limit} seconds. Try a more specific brief or another source.")


def subtitle_stamp(seconds):
    millis = round(max(0, seconds) * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_captions(path, cues):
    blocks = []
    for cue in cues:
        if cue["end"] <= cue["start"]:
            continue
        text = "\n".join(textwrap.wrap(cue["text"], 40))
        blocks.append(f"{len(blocks) + 1}\n{subtitle_stamp(cue['start'])} --> {subtitle_stamp(cue['end'])}\n{text}\n")
    Path(path).write_text("\n".join(blocks), encoding="utf-8")


def write_soundtrack(path, duration):
    rate = 48_000
    chords = [(130.81, 164.81, 196.00), (110.00, 130.81, 164.81), (87.31, 110.00, 130.81), (98.00, 123.47, 146.83)]
    payload = bytearray()
    for frame in range(math.ceil(duration * rate)):
        t = frame / rate
        envelope = min(1.0, t / 0.8, max(0.0, (duration - t) / 1.3))
        notes = chords[int(t / 4) % len(chords)]
        # A slow raised-cosine pulse has no hard attacks or borrowed recordings.
        pulse = 0.55 + 0.45 * math.sin(math.pi * (t % 4) / 4) ** 2
        value = sum(math.sin(math.tau * note * t) for note in notes) / 3
        if t >= 4 and t % 4 < 0.4:
            previous = chords[(int(t / 4) - 1) % len(chords)]
            prior = sum(math.sin(math.tau * note * t) for note in previous) / 3
            mix = (1 - math.cos(math.pi * (t % 4) / 0.4)) / 2
            value = prior * (1 - mix) + value * mix
        sample = int(value * envelope * pulse * 0.075 * 32767)
        payload.extend(struct.pack("<hh", sample, sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(payload)
