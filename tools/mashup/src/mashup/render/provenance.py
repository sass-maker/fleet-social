"""Dependency-free source headings and watermarks for rendered clips.

The supported FFmpeg build has no drawtext or libass subtitle filter. A tiny
bitmap alphabet written into transparent PNGs keeps branding portable without
Pillow, ImageMagick, a platform font, or another production dependency.
"""

from __future__ import annotations

import binascii
import hashlib
import re
import struct
import unicodedata
import zlib
from pathlib import Path

from mashup.models import Clip, VisualInsert

_FONT: dict[str, tuple[str, ...]] = {
    " ": ("00000",) * 7,
    "'": ("00100", "00100", "00000", "00000", "00000", "00000", "00000"),
    ",": ("00000", "00000", "00000", "00000", "00100", "00100", "01000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00100", "00100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    "|": ("00100",) * 7,
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00001", "00001", "00001", "00001", "10001", "10001", "01110"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}

_PANEL = (10, 10, 14, 210)
_FOREGROUND = (244, 241, 232, 255)
_MUTED = (190, 187, 180, 235)
_GOLD = (211, 174, 93, 255)
_SPACE = re.compile(r"\s+")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def source_timecode(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _ascii_title(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value)
    ascii_text = normalised.encode("ascii", "ignore").decode().upper()
    return _SPACE.sub(" ", ascii_text).strip()


def label_text(clip: Clip, *, max_chars: int) -> str:
    title, interval = _label_lines(clip, max_chars=max_chars)
    return f"{title} | {interval}"


def _label_lines(clip: Clip, *, max_chars: int) -> tuple[str, str]:
    interval = f"{source_timecode(clip.start)} - {source_timecode(clip.end)}"
    title = _ascii_title(clip.source_title or clip.source_id) or "UNKNOWN SOURCE"
    if len(title) > max_chars:
        title = title[: max(1, max_chars - 1)].rstrip() + "-"
    return title, interval


def _title_scale(width: int) -> int:
    if width >= 900:
        return 4
    if width >= 500:
        return 3
    return 2


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _png_bytes(width: int, height: int, pixels: bytearray) -> bytes:
    rows = b"".join(
        b"\x00" + bytes(pixels[y * width * 4 : (y + 1) * width * 4]) for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _fill_panel(
    pixels: bytearray,
    width: int,
    height: int,
    color: tuple[int, int, int, int],
) -> None:
    pixels[:] = bytearray(color * (width * height))


def _draw_text(
    pixels: bytearray,
    width: int,
    height: int,
    text: str,
    *,
    x: int,
    y: int,
    scale: int,
    color: tuple[int, int, int, int],
) -> None:
    advance = 6 * scale
    for position, character in enumerate(text):
        glyph = _FONT.get(character, _FONT[" "])
        origin_x = x + position * advance
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px = origin_x + column * scale + dx
                        py = y + row * scale + dy
                        if px < 0 or py < 0 or px >= width or py >= height:
                            continue
                        offset = (py * width + px) * 4
                        pixels[offset : offset + 4] = bytes(color)


def _write_png(path: Path, width: int, height: int, pixels: bytearray) -> Path:
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(_png_bytes(width, height, pixels))
    temporary.replace(path)
    return path


def write_label_card(clip: Clip, width: int, directory: Path) -> Path:
    """Write and return a deterministic transparent archive heading."""
    title_scale = _title_scale(width)
    small_scale = max(2, title_scale - 2)
    padding = 4 * title_scale
    title_advance = 6 * title_scale
    max_chars = max(12, (max(120, width - 32) - 2 * padding) // title_advance)
    title, interval = _label_lines(clip, max_chars=max_chars)
    kicker = "FROM THE ARCHIVE"
    card_width = min(
        width - 16,
        2 * padding
        + max(
            len(kicker) * 6 * small_scale,
            len(title) * title_advance,
            len(interval) * 6 * small_scale,
        ),
    )
    card_height = 2 * padding + 14 * small_scale + 9 * title_scale

    identity = hashlib.sha256(
        f"premium-v1|{width}|{title_scale}|{title}|{interval}".encode()
    ).hexdigest()[:20]
    path = Path(directory) / f"source-{identity}.png"
    pixels = bytearray(card_width * card_height * 4)
    _fill_panel(pixels, card_width, card_height, _PANEL)

    cursor = padding
    _draw_text(
        pixels,
        card_width,
        card_height,
        kicker,
        x=padding,
        y=cursor,
        scale=small_scale,
        color=_GOLD,
    )
    cursor += 7 * small_scale + 2 * title_scale
    _draw_text(
        pixels,
        card_width,
        card_height,
        title,
        x=padding,
        y=cursor,
        scale=title_scale,
        color=_FOREGROUND,
    )
    cursor += 8 * title_scale
    _draw_text(
        pixels,
        card_width,
        card_height,
        interval,
        x=padding,
        y=cursor,
        scale=small_scale,
        color=_MUTED,
    )
    return _write_png(path, card_width, card_height, pixels)


def write_watermark_card(text: str, width: int, directory: Path) -> Path:
    """Write a small transparent watermark card."""
    scale = 3 if width >= 900 else 2
    value = _ascii_title(text)[:28] or "MASHUP"
    padding = 2 * scale
    card_width = 2 * padding + len(value) * 6 * scale - scale
    card_height = 2 * padding + 7 * scale
    identity = hashlib.sha256(f"watermark-v1|{width}|{scale}|{value}".encode()).hexdigest()[:20]
    path = Path(directory) / f"watermark-{identity}.png"
    pixels = bytearray(card_width * card_height * 4)
    _draw_text(
        pixels,
        card_width,
        card_height,
        value,
        x=padding,
        y=padding,
        scale=scale,
        color=(244, 241, 232, 145),
    )
    return _write_png(path, card_width, card_height, pixels)


def write_visual_credit_card(visual: VisualInsert, width: int, directory: Path) -> Path:
    """Write the interval-bound credit shown over an archival still."""
    scale = 2 if width >= 500 else 1
    padding = 3 * scale
    prefix = "ARCHIVAL VISUAL"
    title = _ascii_title(visual.source_title)[:44] or "UNKNOWN SOURCE"
    card_width = min(
        width - 16,
        2 * padding + max(len(prefix), len(title)) * 6 * scale,
    )
    card_height = 2 * padding + 16 * scale
    identity = hashlib.sha256(f"visual-credit-v1|{width}|{scale}|{title}".encode()).hexdigest()[:20]
    path = Path(directory) / f"visual-credit-{identity}.png"
    pixels = bytearray(card_width * card_height * 4)
    _fill_panel(pixels, card_width, card_height, (10, 10, 14, 190))
    _draw_text(
        pixels,
        card_width,
        card_height,
        prefix,
        x=padding,
        y=padding,
        scale=scale,
        color=_GOLD,
    )
    _draw_text(
        pixels,
        card_width,
        card_height,
        title,
        x=padding,
        y=padding + 9 * scale,
        scale=scale,
        color=_MUTED,
    )
    return _write_png(path, card_width, card_height, pixels)
