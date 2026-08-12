from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from birddisplay.render.palette import (
    Ink,
    WIRE_CODES,
    fit_cover,
    new_canvas,
    pack_for_panel,
    prepare_photo,
    quantize_photo,
    to_rgb,
)


def photo(width=600, height=400) -> Image.Image:
    """A synthetic photo with smooth gradients and saturated patches."""
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for x in range(width):
        shade = int(255 * x / width)
        draw.line([(x, 0), (x, height)], fill=(shade, 255 - shade, 128))
    draw.ellipse((100, 80, 300, 260), fill=(200, 40, 30))
    return image


def test_the_canvas_starts_white_and_palettised(config):
    canvas = new_canvas(800, 480, config.palette)
    assert canvas.mode == "P"
    assert canvas.size == (800, 480)
    assert set(canvas.tobytes()) == {int(Ink.WHITE)}


def test_text_lands_in_exact_ink_indices(config):
    # Dithered small text is unreadable, so text must never be dithered.
    canvas = new_canvas(200, 60, config.palette)
    draw = ImageDraw.Draw(canvas)
    draw.text((5, 5), "Dunnock", fill=int(Ink.RED))
    used = set(canvas.tobytes())
    assert used <= {int(Ink.WHITE), int(Ink.RED)}
    assert int(Ink.RED) in used


def test_quantising_uses_only_the_six_inks(config):
    quantised = quantize_photo(photo(), config.image, config.palette)
    assert quantised.mode == "P"
    assert set(quantised.tobytes()) <= set(range(6))


def test_dithering_mixes_inks_where_flat_quantising_would_band(config):
    dithered = quantize_photo(photo(), config.image, config.palette, dither=True)
    flat = quantize_photo(photo(), config.image, config.palette, dither=False)
    assert len(set(dithered.tobytes())) > len(set(flat.tobytes()))


def test_fit_cover_crops_to_exactly_the_slot(config):
    assert fit_cover(photo(600, 400), 480, 480).size == (480, 480)
    assert fit_cover(photo(200, 900), 480, 480).size == (480, 480)
    assert fit_cover(photo(480, 480), 480, 480).size == (480, 480)


def test_prepare_photo_returns_a_pasteable_panel_slot(config):
    prepared = prepare_photo(photo(), 480, 480, config.image, config.palette)
    assert prepared.mode == "P"
    assert prepared.size == (480, 480)


def test_a_photo_pasted_into_a_canvas_keeps_its_ink_indices(config):
    canvas = new_canvas(800, 480, config.palette)
    prepared = prepare_photo(photo(), 480, 480, config.image, config.palette)
    canvas.paste(prepared, (0, 0))
    assert canvas.crop((0, 0, 480, 480)).tobytes() == prepared.tobytes()


def test_packing_puts_two_pixels_in_each_byte(config):
    canvas = new_canvas(4, 2, config.palette)
    canvas.putpixel((0, 0), int(Ink.BLACK))
    canvas.putpixel((1, 0), int(Ink.WHITE))
    canvas.putpixel((2, 0), int(Ink.YELLOW))
    canvas.putpixel((3, 0), int(Ink.RED))
    canvas.putpixel((0, 1), int(Ink.BLUE))
    canvas.putpixel((1, 1), int(Ink.GREEN))

    packed = pack_for_panel(canvas)
    assert len(packed) == 4
    assert packed[0] == 0x01  # black, white
    assert packed[1] == 0x23  # yellow, red
    # Blue and green are wire codes 5 and 6, not 4 and 5: the panel's
    # code 4 is orange on a 7in3f and undefined here, so we skip it.
    assert packed[2] == 0x56


def test_no_ink_ever_packs_to_the_reserved_code_four(config):
    assert 4 not in WIRE_CODES
    canvas = new_canvas(800, 480, config.palette)
    canvas.paste(prepare_photo(photo(), 800, 480, config.image, config.palette), (0, 0))
    packed = pack_for_panel(canvas)
    nibbles = {byte >> 4 for byte in packed} | {byte & 0x0F for byte in packed}
    assert 4 not in nibbles
    assert nibbles <= set(WIRE_CODES)


def test_a_full_board_packs_to_the_size_the_panel_expects(config):
    canvas = new_canvas(800, 480, config.palette)
    assert len(pack_for_panel(canvas)) == 800 * 480 // 2


def test_packing_rejects_an_unpalettised_image():
    with pytest.raises(ValueError, match="palettised"):
        pack_for_panel(Image.new("RGB", (800, 480)))


def test_preview_rendering_uses_the_display_palette_not_the_match_one(config):
    canvas = new_canvas(2, 1, config.palette)
    canvas.putpixel((0, 0), int(Ink.RED))
    canvas.putpixel((1, 0), int(Ink.BLUE))
    rgb = to_rgb(canvas, config.palette)
    assert rgb.getpixel((0, 0)) == config.palette.red
    assert rgb.getpixel((1, 0)) == config.palette.blue


def test_match_palette_keeps_mid_greys_neutral(config):
    # Matching against muted ink values drags every neutral onto a
    # coloured ink and photographs come out as confetti. Regression test
    # for that, using a flat mid-grey: it must dither to black and white.
    grey = Image.new("RGB", (32, 32), (128, 128, 128))
    quantised = quantize_photo(grey, config.image, config.palette)
    assert set(quantised.tobytes()) <= {int(Ink.BLACK), int(Ink.WHITE)}
