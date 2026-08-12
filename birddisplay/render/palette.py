"""Spectra 6 palette, photo dithering, and panel packing.

Two rules govern everything here:

1. Photos are dithered; text is not. Floyd-Steinberg on 10px text is
   unreadable mush. So the canvas is a palette ("P" mode) image from the
   start -- text is drawn straight into palette indices, and the photo is
   quantised separately and pasted in.

2. Our index order is the panel's index order. epd7in3e packs two 4-bit
   colour codes per byte, and those codes are palette indices, so keeping
   the orders aligned means the pack step is a lookup and nothing else.

The panel's own code table (from the Waveshare driver) is:

    0 black   1 white   2 yellow   3 red   4 (unused)   5 blue   6 green

Note the gap at 4. We use a gapless 0-5 internally and map through
WIRE_CODES when packing, which also guarantees we never emit code 4 --
on a 7in3f that code is orange, and on a 7in3e it is undefined.
"""

from __future__ import annotations

from enum import IntEnum

from PIL import Image, ImageEnhance

from ..config import NOMINAL_MATCH_RGB, ImageConfig, PaletteConfig


class Ink(IntEnum):
    """Palette index of each ink. Use these as `fill=` when drawing."""

    BLACK = 0
    WHITE = 1
    YELLOW = 2
    RED = 3
    BLUE = 4
    GREEN = 5


# Internal index -> the 4-bit code the panel expects on the wire.
WIRE_CODES = (0x0, 0x1, 0x2, 0x3, 0x5, 0x6)

# Fallback RGB values, used when no PaletteConfig is supplied (tests,
# scripts). config.toml is the place to tune these.
DEFAULT_RGB: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (255, 255, 255),
    (238, 202, 44),
    (172, 42, 40),
    (42, 66, 138),
    (42, 106, 74),
)


def rgb_table(palette: PaletteConfig | None = None) -> list[tuple[int, int, int]]:
    return list(palette.as_list()) if palette else list(DEFAULT_RGB)


def flat_palette(palette: PaletteConfig | None = None) -> list[int]:
    """The 768-entry flat palette Pillow's putpalette() wants.

    Unused slots repeat white rather than black: if a stray index ever
    escapes into the image, a white pixel is far less visible than a
    black one, and white is the panel's rest state.
    """
    table = rgb_table(palette)
    flat: list[int] = []
    for colour in table:
        flat.extend(colour)
    white = table[Ink.WHITE]
    flat.extend(list(white) * (256 - len(table)))
    return flat


def palette_image(palette: PaletteConfig | None = None) -> Image.Image:
    """A 1x1 "P" image carrying the *match* palette, for Image.quantize().

    Deliberately not the display palette. See PaletteConfig: matching
    against the muted colours the panel really prints pulls every neutral
    mid-tone onto a coloured ink and the photo turns to confetti.
    """
    table = list(palette.match) if palette else list(NOMINAL_MATCH_RGB)
    pal = Image.new("P", (1, 1))
    # Only the six real inks are declared, so quantize() cannot pick a
    # padding slot as the nearest colour.
    flat: list[int] = []
    for colour in table:
        flat.extend(colour)
    pal.putpalette(flat + [0, 0, 0] * (256 - len(table)))
    return pal


def new_canvas(
    width: int,
    height: int,
    palette: PaletteConfig | None = None,
    background: Ink = Ink.WHITE,
) -> Image.Image:
    """A blank palettised board. Draw text onto this directly."""
    canvas = Image.new("P", (width, height), int(background))
    canvas.putpalette(flat_palette(palette))
    return canvas


def enhance_photo(image: Image.Image, settings: ImageConfig) -> Image.Image:
    """Pre-process a photo so it survives being reduced to six colours.

    Raw photos dither badly -- mid-tones smear into noise. Pushing
    saturation and contrast first gives the error diffusion something to
    aim at, and a light sharpen keeps feather detail from dissolving.
    """
    out = image.convert("RGB")
    if settings.saturation != 1.0:
        out = ImageEnhance.Color(out).enhance(settings.saturation)
    if settings.contrast != 1.0:
        out = ImageEnhance.Contrast(out).enhance(settings.contrast)
    if settings.sharpness != 1.0:
        out = ImageEnhance.Sharpness(out).enhance(settings.sharpness)
    return out


def fit_cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale and centre-crop to exactly width x height, preserving aspect.

    Crops slightly above centre: in a bird photo the subject is usually in
    the upper half, and cropping dead centre tends to cut its head off.
    """
    src_w, src_h = image.size
    if src_w == 0 or src_h == 0:
        raise ValueError("cannot fit a zero-sized image")
    scale = max(width / src_w, height / src_h)
    new_size = (max(1, round(src_w * scale)), max(1, round(src_h * scale)))
    resized = image.resize(new_size, Image.Resampling.LANCZOS)

    left = (new_size[0] - width) // 2
    top = int((new_size[1] - height) * 0.4)
    return resized.crop((left, top, left + width, top + height))


def quantize_photo(
    image: Image.Image,
    settings: ImageConfig,
    palette: PaletteConfig | None = None,
    dither: bool = True,
) -> Image.Image:
    """Enhance, then reduce to the six inks with Floyd-Steinberg."""
    prepared = enhance_photo(image, settings)
    return prepared.quantize(
        palette=palette_image(palette),
        dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE,
    )


def prepare_photo(
    image: Image.Image,
    width: int,
    height: int,
    settings: ImageConfig,
    palette: PaletteConfig | None = None,
) -> Image.Image:
    """Crop to the panel slot and dither. Returns a "P" image."""
    return quantize_photo(fit_cover(image, width, height), settings, palette)


def to_rgb(canvas: Image.Image, palette: PaletteConfig | None = None) -> Image.Image:
    """Render a palettised board as RGB, for preview PNGs.

    Approximate by definition: a backlit monitor cannot show what reflective
    e-ink looks like. Tune against the panel, not against this.
    """
    working = canvas.copy()
    working.putpalette(flat_palette(palette))
    return working.convert("RGB")


def pack_for_panel(canvas: Image.Image) -> bytearray:
    """Pack a "P" board into the panel's two-pixels-per-byte buffer.

    Deliberately bypasses the driver's own getbuffer(), which re-quantises
    with dithering and would turn our carefully aliased text back to mush.
    """
    if canvas.mode != "P":
        raise ValueError(f"expected a palettised image, got mode {canvas.mode}")
    width, height = canvas.size
    if width % 2:
        raise ValueError("panel width must be even to pack two pixels per byte")

    pixels = canvas.tobytes("raw")
    expected = width * height
    if len(pixels) != expected:
        raise ValueError(f"expected {expected} pixels, got {len(pixels)}")

    lookup = bytes(
        WIRE_CODES[i] if i < len(WIRE_CODES) else WIRE_CODES[Ink.WHITE]
        for i in range(256)
    )
    mapped = pixels.translate(lookup)
    buffer = bytearray(expected // 2)
    buffer[:] = bytes(
        (mapped[i] << 4) | mapped[i + 1] for i in range(0, expected, 2)
    )
    return buffer
