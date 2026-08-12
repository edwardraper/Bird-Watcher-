"""Font loading, measurement and wrapping.

Fonts are resolved at runtime from a candidate list rather than pinned to
one path, because the panel's Pi and your laptop rarely agree on where
fonts live. Drop a TTF into assets/fonts/ to override the system choice.

On a fresh Raspberry Pi OS Lite:

    sudo apt install -y fonts-dejavu-core fonts-liberation2
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

from PIL import ImageFont

log = logging.getLogger(__name__)

ASSET_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"

SYSTEM_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/freefont"),
    Path("/usr/share/fonts/truetype"),
    Path("/usr/share/fonts"),
    Path("/Library/Fonts"),
    Path("C:/Windows/Fonts"),
)

# Style -> candidate filenames, best first. Every style ends with a
# candidate that ships in fonts-dejavu-core, so nothing is ever missing
# on a stock Pi -- italic just quietly degrades to upright.
FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "sans": ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf"),
    "sans_bold": (
        "DejaVuSans-Bold.ttf",
        "LiberationSans-Bold.ttf",
        "FreeSansBold.ttf",
        "DejaVuSans.ttf",
    ),
    "serif": ("DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "FreeSerif.ttf"),
    "serif_italic": (
        "DejaVuSerif-Italic.ttf",
        "LiberationSerif-Italic.ttf",
        "FreeSerifItalic.ttf",
        "DejaVuSerif.ttf",
    ),
    # Times New Roman itself is Monotype's and ships with neither Raspberry
    # Pi OS nor most Linux boxes. Liberation Serif is metric-compatible with
    # it and is what a Pi actually has, so it leads; drop the real thing
    # into assets/fonts/ and it wins, because that directory is searched
    # first. DejaVu Serif last: wider and rounder, but always present.
    "times": (
        "Times New Roman.ttf",
        "times.ttf",
        "LiberationSerif-Regular.ttf",
        "DejaVuSerif.ttf",
    ),
    "times_italic": (
        "Times New Roman Italic.ttf",
        "timesi.ttf",
        "LiberationSerif-Italic.ttf",
        "DejaVuSerif-Italic.ttf",
    ),
    "times_bold": (
        "Times New Roman Bold.ttf",
        "timesbd.ttf",
        "LiberationSerif-Bold.ttf",
        "DejaVuSerif-Bold.ttf",
    ),
}

ELLIPSIS = "…"


class FontsUnavailable(Exception):
    """No usable TTF found for a style."""


def _search_dirs(extra: Iterable[Path] = ()) -> list[Path]:
    return [ASSET_DIR, *extra, *SYSTEM_FONT_DIRS]


@lru_cache(maxsize=None)
def find_font_file(style: str) -> Path:
    """Locate a TTF for a style, preferring assets/fonts/ over the system."""
    candidates = FONT_CANDIDATES.get(style)
    if not candidates:
        raise FontsUnavailable(f"unknown font style {style!r}")

    for name in candidates:
        for directory in _search_dirs():
            path = directory / name
            if path.exists():
                return path
    # Last resort: any TTF at all in assets/, so a vendored font with an
    # unexpected name still works.
    for path in sorted(ASSET_DIR.glob("*.ttf")):
        log.warning("no standard font for %r; falling back to %s", style, path)
        return path
    raise FontsUnavailable(
        f"no font file found for style {style!r}. Install fonts-dejavu-core "
        f"or drop a TTF into {ASSET_DIR}"
    )


@lru_cache(maxsize=128)
def load_font(style: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(find_font_file(style)), size)


class FontBook:
    """The board's type scale, in one place.

    Sizes are chosen for a 7.3in 800x480 panel viewed from across a room:
    nothing below 11px, and the species name as large as will fit.
    """

    def __init__(self) -> None:
        self.headline = load_font("sans_bold", 40)
        self.title = load_font("sans_bold", 26)
        self.scientific = load_font("serif_italic", 17)
        self.body = load_font("sans", 16)
        self.body_bold = load_font("sans_bold", 16)
        self.label = load_font("sans_bold", 13)
        self.small = load_font("sans", 14)
        self.tiny = load_font("sans", 11)

    @staticmethod
    def sized(style: str, size: int) -> ImageFont.FreeTypeFont:
        return load_font(style, size)


def text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    """Advance width, which is what matters for laying text out."""
    return font.getlength(text)


def line_height(font: ImageFont.FreeTypeFont) -> int:
    """Height of one line, using the font's own metrics.

    Using ascent+descent rather than a measured bbox keeps line spacing
    even whether or not a particular line happens to contain a descender.
    """
    ascent, descent = font.getmetrics()
    return ascent + descent


def truncate(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """Shorten text with an ellipsis until it fits."""
    if text_width(text, font) <= max_width:
        return text
    trimmed = text
    while trimmed and text_width(trimmed + ELLIPSIS, font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + ELLIPSIS) if trimmed else ""


def _break_word(
    word: str, font: ImageFont.FreeTypeFont, max_width: float
) -> list[str]:
    """Split a word too wide for the column into chunks that fit."""
    if text_width(word, font) <= max_width:
        return [word]
    chunks: list[str] = []
    remaining = word
    while remaining and text_width(remaining, font) > max_width:
        piece = remaining
        while len(piece) > 1 and text_width(piece, font) > max_width:
            piece = piece[:-1]
        chunks.append(piece)
        remaining = remaining[len(piece) :]
    if remaining:
        chunks.append(remaining)
    return chunks


def wrap(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    max_lines: int | None = None,
) -> list[str]:
    """Greedy word wrap. Over-long single words are broken mid-word.

    If max_lines is given, the last line is ellipsised rather than dropped,
    so the reader can tell something was cut.
    """
    words: list[str] = []
    for word in text.split():
        words.extend(_break_word(word, font, max_width))
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or text_width(candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    if max_lines is not None and len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1] = truncate(" ".join(lines[max_lines - 1 :]), font, max_width)
        return kept
    return lines


def fit_single_line(
    text: str,
    style: str,
    max_width: float,
    sizes: Sequence[int],
) -> tuple[ImageFont.FreeTypeFont, str]:
    """Pick the largest size from sizes that fits text on one line.

    Falls back to the smallest size with an ellipsis if nothing fits --
    used for very long species names like "Eurasian Reed Warbler".
    """
    for size in sorted(sizes, reverse=True):
        font = load_font(style, size)
        if text_width(text, font) <= max_width:
            return font, text
    font = load_font(style, min(sizes))
    return font, truncate(text, font, max_width)
