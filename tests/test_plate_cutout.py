"""Background removal, against synthetic plates with known answers.

Real plates are the only honest test of how this looks, but they are not a
test of whether it works. These are: a blob on cream paper with a caption
under it, and the two things that go wrong -- a plate that is all picture,
and white inside the subject that must not be eaten.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

pytest.importorskip("numpy")
pytest.importorskip("scipy", reason="cut_out is build-time only; needs scipy")

from scripts.plate_cutout import cut_out, fit_within  # noqa: E402

PAPER = (243, 238, 224)
INK = (60, 70, 40)


def plate(
    size: tuple[int, int] = (600, 800),
    caption: bool = True,
    white_patch: bool = False,
) -> Image.Image:
    """Cream paper, a bird-ish shape, and small marks for a caption.

    Bird-ish rather than a blob on purpose: a filled ellipse covers 78% of
    its own bounding box, which is the signature of a plate with a painted
    background. A body with a tail and legs lands where real birds land.
    """
    image = Image.new("RGB", size, PAPER)
    draw = ImageDraw.Draw(image)
    draw.ellipse((200, 150, 340, 400), fill=INK)
    draw.polygon([(320, 320), (430, 450), (400, 470), (300, 380)], fill=INK)
    draw.line((260, 390, 250, 470), fill=INK, width=5)
    draw.line((290, 390, 300, 470), fill=INK, width=5)
    if white_patch:
        # A gull's wing: paper-coloured, but enclosed by the bird.
        draw.ellipse((240, 220, 300, 300), fill=(252, 250, 246))
    if caption:
        for index in range(12):
            x = 190 + index * 18
            draw.rectangle((x, 620, x + 9, 636), fill=(20, 20, 20))
    return image


def alpha_at(image: Image.Image, x: int, y: int) -> int:
    return image.split()[3].getpixel((x, y))


def opaque_pixels(image: Image.Image) -> int:
    return sum(image.split()[3].histogram()[129:])


def test_the_paper_goes_and_the_subject_stays() -> None:
    result = cut_out(plate())
    assert result.kept_blobs == 1
    # Cropped to the bird, with a small margin, not to the whole page.
    assert result.image.width < 280 and result.image.height < 400
    assert alpha_at(result.image, result.image.width // 3, result.image.height // 4) > 200


def test_the_caption_is_dropped() -> None:
    with_caption = cut_out(plate())
    without = cut_out(plate(caption=False))
    assert with_caption.dropped_blobs >= 12
    # Both crop to the same subject: the caption never reaches the output.
    assert abs(with_caption.image.height - without.image.height) <= 2


def test_corners_become_transparent() -> None:
    result = cut_out(plate())
    assert alpha_at(result.image, 1, 1) < 60


def test_white_enclosed_by_the_subject_survives() -> None:
    """A gull's wing is paper-coloured and must not be punched out.

    The mask is "paper-coloured *and* connected to the border", so white
    with the bird all round it is never reached. Stated as an area rather
    than a probe pixel: the cutout keeps the same amount of bird either
    way.
    """
    plain = cut_out(plate())
    with_white = cut_out(plate(white_patch=True))
    assert with_white.image.size == plain.image.size
    kept = opaque_pixels(with_white.image)
    assert abs(kept - opaque_pixels(plain.image)) / kept < 0.02


def test_a_full_bleed_picture_is_reported_as_a_scene() -> None:
    """Keulemans painted backgrounds into some plates; there is nothing
    to remove, and the caller needs to know that rather than be told the
    cutout worked."""
    scene = Image.new("RGB", (600, 800), (120, 150, 90))
    result = cut_out(scene)
    assert result.is_scene
    assert result.coverage > 0.9


def test_a_bird_on_paper_is_not_reported_as_a_scene() -> None:
    result = cut_out(plate())
    assert not result.is_scene
    assert not result.failed


def test_paper_colour_is_estimated_from_the_border() -> None:
    result = cut_out(plate())
    assert all(abs(a - b) < 12 for a, b in zip(result.paper_rgb, PAPER))


def test_a_dark_scanner_edge_does_not_move_the_estimate() -> None:
    """A median, not a mean: one dark gutter must not drag the paper
    colour dark enough to take the whole page with it."""
    image = plate()
    ImageDraw.Draw(image).rectangle((0, 0, 24, image.height), fill=(30, 25, 20))
    result = cut_out(image)
    assert all(abs(a - b) < 12 for a, b in zip(result.paper_rgb, PAPER))
    assert result.kept_blobs == 1


def test_fit_within_only_shrinks() -> None:
    image = Image.new("RGB", (1200, 800))
    assert fit_within(image, 600).size == (600, 400)
    assert fit_within(image, 5000).size == (1200, 800)
