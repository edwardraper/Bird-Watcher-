"""The export the Inky Frame fetches.

The frame decodes this with pngdec in PNG_COPY mode, which copies palette
indices straight into the framebuffer. So the indices are the payload, and
these tests are about the payload arriving unchanged: right order, right
count, and nothing helpfully renumbering them on the way to disk.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from birddisplay.config import Config
from birddisplay.display.inky_export import InkyExportDisplay
from birddisplay.render.palette import (
    INKY_PEN_CODES,
    Ink,
    new_canvas,
    rgb_table,
    to_inky_png,
)

# Pimoroni's PicoGraphics pen order for PEN_3BIT, from the picographics
# README: BLACK 0, WHITE 1, GREEN 2, BLUE 3, RED 4, YELLOW 5.
EXPECTED_PEN = {
    Ink.BLACK: 0,
    Ink.WHITE: 1,
    Ink.GREEN: 2,
    Ink.BLUE: 3,
    Ink.RED: 4,
    Ink.YELLOW: 5,
}


def striped_board(config: Config) -> Image.Image:
    """A board using every ink, one vertical band each."""
    canvas = new_canvas(config.display.width, config.display.height, config.palette)
    band = config.display.width // len(Ink)
    pixels = canvas.load()
    for ink in Ink:
        for x in range(int(ink) * band, (int(ink) + 1) * band):
            for y in range(config.display.height):
                pixels[x, y] = int(ink)
    return canvas


@pytest.fixture
def board_image(config: Config) -> Image.Image:
    return striped_board(config)


def test_every_ink_lands_on_the_right_pen(config: Config, board_image) -> None:
    exported = to_inky_png(board_image, config.palette)
    band = config.display.width // len(Ink)
    for ink, pen in EXPECTED_PEN.items():
        x = int(ink) * band + band // 2
        assert exported.getpixel((x, 10)) == pen, f"{ink.name} went to the wrong pen"


def test_the_lookup_table_matches_the_expected_order() -> None:
    """Stated twice on purpose: once as the table the code uses, once as
    Pimoroni's documented order. If they drift apart the board comes out
    with green sky."""
    for ink, pen in EXPECTED_PEN.items():
        assert INKY_PEN_CODES[int(ink)] == pen


def test_the_palette_describes_those_pens(config: Config, board_image) -> None:
    """So the PNG is viewable as a picture, even though the frame only
    reads indices."""
    exported = to_inky_png(board_image, config.palette)
    palette = exported.getpalette()
    rgb = rgb_table(config.palette)
    for ink, pen in EXPECTED_PEN.items():
        assert tuple(palette[pen * 3 : pen * 3 + 3]) == rgb[int(ink)]


def test_export_is_palettised_and_panel_sized(
    config: Config, board_image, tmp_path: Path
) -> None:
    display = InkyExportDisplay(config, path=tmp_path / "board.png")
    display.show(board_image)

    with Image.open(display.path) as reopened:
        assert reopened.mode == "P"
        assert reopened.size == (config.display.width, config.display.height)


def test_saving_does_not_renumber_the_indices(
    config: Config, board_image, tmp_path: Path
) -> None:
    """The failure this guards against is silent: Pillow's optimize=True
    drops unused palette entries and renumbers the rest, which would send
    a board of correct-looking colours to the wrong pens."""
    display = InkyExportDisplay(config, path=tmp_path / "board.png")
    display.show(board_image)

    expected = to_inky_png(board_image, config.palette)
    with Image.open(display.path) as reopened:
        assert reopened.tobytes() == expected.tobytes()


def test_manifest_matches_the_file(config: Config, board_image, tmp_path: Path) -> None:
    display = InkyExportDisplay(config, path=tmp_path / "board.png")
    display.headline = "Robin"
    display.show(board_image)

    manifest = json.loads(display.manifest_path.read_text())
    payload = display.path.read_bytes()
    assert manifest["sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["bytes"] == len(payload)
    assert manifest["headline"] == "Robin"
    assert manifest["image"] == "board.png"
    assert manifest["width"] == config.display.width


def test_an_unchanged_board_keeps_its_sha(
    config: Config, board_image, tmp_path: Path
) -> None:
    """A frame skips the download and the 30s refresh when the sha is the
    same, so the same board must not produce a different one."""
    first = InkyExportDisplay(config, path=tmp_path / "a.png")
    second = InkyExportDisplay(config, path=tmp_path / "b.png")
    first.show(board_image)
    second.show(board_image)
    assert first.path.read_bytes() == second.path.read_bytes()


def test_nothing_is_left_half_written(
    config: Config, board_image, tmp_path: Path
) -> None:
    display = InkyExportDisplay(config, path=tmp_path / "board.png")
    display.show(board_image)
    assert not list(tmp_path.glob("*.part"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["board.json", "board.png"]


def test_rotation_is_applied(config: Config, board_image, tmp_path: Path) -> None:
    rotated_config = replace(
        config, display=replace(config.display, rotate=180)
    )
    display = InkyExportDisplay(rotated_config, path=tmp_path / "board.png")
    display.show(board_image)

    band = config.display.width // len(Ink)
    with Image.open(display.path) as reopened:
        # The leftmost band was black; upside down it is the rightmost.
        assert reopened.getpixel((config.display.width - band // 2, 10)) == 0


def test_a_wrong_sized_board_is_refused(config: Config, tmp_path: Path) -> None:
    display = InkyExportDisplay(config, path=tmp_path / "board.png")
    with pytest.raises(ValueError, match="frame expects"):
        display.show(Image.new("P", (100, 100)))


def test_only_palettised_boards_can_be_exported(config: Config) -> None:
    with pytest.raises(ValueError, match="palettised"):
        to_inky_png(Image.new("RGB", (10, 10)), config.palette)
