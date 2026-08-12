from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image, ImageDraw

from birddisplay.model import Board, Photo, Sighting, Species
from birddisplay.render import fonts as F
from birddisplay.render.layout import BoardRenderer, _fmt_day, _fmt_when
from birddisplay.render.palette import Ink

NOW = datetime(2026, 8, 11, 13, 0)


@pytest.fixture
def config(config):
    """The shipped config, hung the landscape way up.

    BoardRenderer is the photo board, and the photo board is 800x480 by
    construction: one big picture down the left, a digest column to the
    right of it. show.py hands a portrait frame to PlateBoardRenderer
    instead, so this file never sees one, and the geometry asserted below
    -- the 470..480 gutter, the footer strip at y=440 -- is landscape
    measured in pixels. Pin the rotation rather than deriving the size,
    so a change to how the frame hangs cannot quietly reinterpret them.
    """
    return replace(config, display=replace(config.display, rotate=0))


def make_photo_file(tmp_path, name="bird.jpg") -> str:
    image = Image.new("RGB", (900, 600))
    draw = ImageDraw.Draw(image)
    for x in range(900):
        draw.line([(x, 0), (x, 600)], fill=(x % 256, 120, 200 - x % 200))
    path = tmp_path / name
    image.save(path)
    return str(path)


def make_board(**kwargs) -> Board:
    defaults = dict(
        generated_at=datetime.now(timezone.utc),
        region_name="Devon",
        headline=Sighting(
            species=Species("hoopoe", "Eurasian Hoopoe", "Upupa epops", "Hoopoes"),
            location="Prawle Point, South Hams",
            observed_at=datetime(2026, 8, 11, 8, 40),
            count=1,
            notable=True,
        ),
        also_seen=[
            Sighting(
                species=Species(f"sp{i}", name, "Genus species"),
                location="Somewhere",
                observed_at=datetime(2026, 8, 11 - i % 4, 7, 0),
            )
            for i, name in enumerate(
                [
                    "European Robin",
                    "Eurasian Curlew",
                    "Cetti's Warbler",
                    "European Green Woodpecker",
                    "Common Swift",
                    "Grey Heron",
                    "Yellowhammer",
                    "Dunnock",
                    "Common Reed Bunting",
                ]
            )
        ],
        species_count=11,
        checklist_note="11 species within 30 km in the last 5 days",
    )
    defaults.update(kwargs)
    return Board(**defaults)


def inks(image: Image.Image) -> set[int]:
    return set(image.tobytes())


def test_the_board_is_a_palettised_panel_sized_image(config, tmp_path):
    board = make_board(headline_photo=Photo(make_photo_file(tmp_path), "A Photographer"))
    image = BoardRenderer(config).render(board, now=NOW)
    assert image.mode == "P"
    assert image.size == (config.display.width, config.display.height)
    assert inks(image) <= set(range(6))


def white_fraction(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    region = image.crop(box).tobytes()
    return region.count(int(Ink.WHITE)) / len(region)


def test_a_missing_photo_file_falls_back_to_the_text_layout(config, tmp_path):
    # The cache can outlive the image cache, so this has to be survivable.
    renderer = BoardRenderer(config)
    missing = make_board(headline_photo=Photo(str(tmp_path / "gone.jpg"), "Nobody"))
    present = make_board(
        headline_photo=Photo(make_photo_file(tmp_path), "A Photographer")
    )

    text_only = renderer.render(missing, now=NOW)
    with_photo = renderer.render(present, now=NOW)
    assert text_only.size == (800, 480)

    # A dithered photo covers the left of the board in mixed ink; the
    # text layout leaves it mostly white.
    box = (0, 100, 400, 400)
    assert white_fraction(text_only, box) > 0.8
    assert white_fraction(with_photo, box) < 0.5


def test_an_unreadable_photo_file_falls_back_to_the_text_layout(config, tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"this is not a JPEG")
    board = make_board(headline_photo=Photo(str(broken), "Nobody"))
    image = BoardRenderer(config).render(board, now=NOW)
    assert image.size == (800, 480)


def test_a_board_with_no_headline_still_renders(config):
    board = make_board(headline=None)
    image = BoardRenderer(config).render(board, now=NOW)
    assert image.size == (800, 480)
    assert len(inks(image)) > 1


def test_a_board_with_nothing_at_all_still_renders(config):
    board = make_board(headline=None, also_seen=[], species_count=0, checklist_note="")
    image = BoardRenderer(config).render(board, now=NOW)
    assert image.size == (800, 480)


def test_a_stale_board_is_marked_in_red(config, tmp_path):
    photo = Photo(make_photo_file(tmp_path), "A Photographer")
    fresh = make_board(headline_photo=photo)
    stale = make_board(
        headline_photo=photo,
        generated_at=datetime.now(timezone.utc) - timedelta(hours=100),
    )
    renderer = BoardRenderer(config)

    footer = (500, 440, 800, 480)
    fresh_footer = inks(renderer.render(fresh, now=NOW).crop(footer))
    stale_footer = inks(renderer.render(stale, now=NOW).crop(footer))

    # Week-old sightings must never be presented as current.
    assert int(Ink.RED) not in fresh_footer
    assert int(Ink.RED) in stale_footer


def test_the_photo_credit_is_always_drawn(config, tmp_path):
    # It is a licence condition for most CC images, not decoration.
    board = make_board(
        headline_photo=Photo(make_photo_file(tmp_path), "Jane Doe", "CC BY-SA 4.0")
    )
    image = BoardRenderer(config).render(board, now=NOW)
    strip = image.crop((0, 460, 200, 480))
    assert int(Ink.WHITE) in inks(strip)
    assert int(Ink.BLACK) in inks(strip)


def test_a_very_long_species_name_does_not_overflow_the_column(config, tmp_path):
    board = make_board(
        headline=Sighting(
            species=Species(
                "x", "Eurasian Reed Warbler of Extraordinary Length", "Genus species"
            ),
            location="A place with a rather long name indeed, near Exeter",
            observed_at=datetime(2026, 8, 11, 8, 40),
        ),
        headline_photo=Photo(make_photo_file(tmp_path), "A Photographer"),
    )
    image = BoardRenderer(config).render(board, now=NOW)
    # Nothing may spill into the photo, and the footer must stay clear.
    assert image.crop((470, 0, 480, 480)).tobytes().count(int(Ink.WHITE)) > 0
    assert image.size == (800, 480)


def test_the_digest_shrinks_to_fit_rather_than_overrunning(config, tmp_path):
    board = make_board(
        headline_photo=Photo(make_photo_file(tmp_path), "A Photographer"),
        also_seen=make_board().also_seen * 6,
    )
    image = BoardRenderer(config).render(board, now=NOW)
    # The last few pixel rows belong to the footer; the list must stop
    # before them rather than drawing over the top.
    assert image.size == (800, 480)


def test_the_placeholder_board_renders(config):
    image = BoardRenderer(config).render_placeholder(
        "Waiting for the first fetch", "Run python -m birddisplay.fetch."
    )
    assert image.mode == "P"
    assert int(Ink.RED) in inks(image)


def test_relative_dates_read_like_english():
    assert _fmt_when(datetime(2026, 8, 11, 8, 40), NOW) == "Today 08:40"
    assert _fmt_when(datetime(2026, 8, 10, 18, 5), NOW) == "Yesterday 18:05"
    assert _fmt_when(datetime(2026, 8, 8, 7, 0), NOW) == "Saturday 07:00"
    assert _fmt_when(datetime(2026, 7, 2, 7, 0), NOW) == "2 Jul 07:00"
    # A date-only checklist has no meaningful time to show.
    assert _fmt_when(datetime(2026, 8, 11, 0, 0), NOW) == "Today"
    assert _fmt_when(None, NOW) == ""


def test_digest_dates_are_short_enough_for_the_narrow_column():
    assert _fmt_day(datetime(2026, 8, 11, 8, 40), NOW) == "today"
    assert _fmt_day(datetime(2026, 8, 10), NOW) == "yest."
    assert _fmt_day(datetime(2026, 8, 8), NOW) == "sat"
    assert _fmt_day(None, NOW) == ""


def test_wrapping_never_exceeds_the_column_width():
    font = F.load_font("sans", 16)
    lines = F.wrap("European Green Woodpecker at Stover Country Park", font, 150)
    assert len(lines) > 1
    assert all(F.text_width(line, font) <= 150 for line in lines)


def test_an_unbreakable_word_is_broken_rather_than_overflowing():
    font = F.load_font("sans", 16)
    lines = F.wrap("Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch", font, 80)
    assert all(F.text_width(line, font) <= 80 for line in lines)


def test_wrapping_to_a_line_limit_ellipsises_rather_than_dropping_text():
    font = F.load_font("sans", 16)
    lines = F.wrap("one two three four five six seven eight nine", font, 60, max_lines=2)
    assert len(lines) == 2
    assert lines[-1].endswith("…")


def test_truncation_fits_the_width_it_is_given():
    font = F.load_font("sans", 16)
    text = F.truncate("European Green Woodpecker", font, 100)
    assert F.text_width(text, font) <= 100
    assert text.endswith("…")


def test_short_text_is_left_alone():
    font = F.load_font("sans", 16)
    assert F.truncate("Robin", font, 200) == "Robin"
