"""The small type on the plate board, and why some of it is bold.

Text on this board is drawn straight into palette indices so it is never
dithered, which means Pillow rasterises it through a 1-bit mask. Nothing
is anti-aliased, so a stroke thinner than a whole pixel does not come out
faint -- it does not come out at all. Liberation Serif is a Times face
with real stroke contrast, and at 12-14px its hairlines are around half a
pixel, which is how "Public domain" ended up reading as "Public dcmain".

These tests pin the two halves of the fix: the letterspaced caps are set
in a bold face, and the lowercase text is deliberately not.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from birddisplay.render import fonts as F
from birddisplay.render.plate_board import PlateBoardRenderer


@pytest.fixture(params=[0, 90], ids=["landscape", "portrait"])
def renderer(request, config) -> PlateBoardRenderer:
    """The plate board hung both ways up. The type scale differs slightly
    between them, and neither orientation may go back to hairlines."""
    turned = replace(config, display=replace(config.display, rotate=request.param))
    return PlateBoardRenderer(turned)


def ink_pixels(text: str, font, tracking: float = 0.0) -> int:
    """Ink laid down drawing text the way the board draws it: 1-bit mask,
    no anti-aliasing, straight into a palettised canvas."""
    canvas = Image.new("P", (900, 60), 1)
    canvas.putpalette([0, 0, 0, 255, 255, 255] + [0] * (254 * 3))
    draw = ImageDraw.Draw(canvas)
    assert draw.fontmode == "1", "a P canvas must rasterise text unsmoothed"
    draw.text((10, 10), text, font=font, fill=0)
    return canvas.tobytes().count(0)


def test_the_letterspaced_caps_are_set_in_a_bold_face(renderer) -> None:
    # The header and the ALSO SEEN label. All caps at 12-14px is exactly
    # where a Times hairline falls under one pixel and vanishes.
    for name, font in (("header", renderer.header), ("label", renderer.label)):
        face = Path(font.path).name
        assert "Bold" in face or "bd" in face.lower(), (
            f"{name} is set in {face}, which is not a bold face -- its "
            "hairlines will be eaten by the 1-bit mask"
        )


def test_the_running_text_is_not_bold(renderer) -> None:
    """The other half of the fix, and the easier one to undo by accident.

    The species list and the time sit at 16-17px, where the hairlines
    already survive. Bolding them buys no legibility and costs the board
    the restraint that is the whole design.
    """
    for name, font in (("listing", renderer.listing), ("detail", renderer.detail)):
        face = Path(font.path).name
        assert "Bold" not in face, f"{name} should stay regular weight, got {face}"


def test_bold_survives_the_1_bit_mask_where_regular_does_not() -> None:
    """The measurement behind the choice, so the reason outlives the memory.

    Same string, same size, one weight apart: the bold face keeps
    materially more ink once the mask has thrown away everything that did
    not cover a whole pixel.
    """
    caps = "BOWLING GREEN MARSH RSPB"
    regular = ink_pixels(caps, F.load_font("times", 14))
    bold = ink_pixels(caps, F.load_font("times_bold", 14))
    assert bold > regular * 1.2, (
        f"bold laid down {bold}px against regular's {regular}px -- if these "
        "are close, the bold face probably failed to resolve and fell back"
    )


def test_the_credit_is_large_enough_to_hold_together(renderer) -> None:
    """Sized up rather than emboldened: a licence line should be legible,
    not loud. Below about 13px this face starts shedding strokes."""
    assert renderer.credit.size >= 13
    assert "Bold" not in Path(renderer.credit.path).name


def test_a_bold_face_is_actually_available(renderer) -> None:
    """times_bold ends its candidate list with DejaVuSerif-Bold, so this
    only fails on a box with no serif bold at all -- at which point the
    board silently loses the fix and should say so."""
    assert Path(renderer.header.path).exists()
