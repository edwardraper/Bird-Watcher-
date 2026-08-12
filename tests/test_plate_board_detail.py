"""What the board says under the scientific name, and what it no longer says.

The observation time and the head count used to sit here. They were true
and neither earned its place on a wall: the panel refreshes every two
hours, so a printed "07:14" answers a question nobody asks of a picture,
and a count of 3 counted one checklist rather than the morning.

"notable" stays, because it is the only thing in that line that changes
how you look at the bird.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from birddisplay.model import Sighting, Species
from birddisplay.render.plate_board import PlateBoardRenderer

WHEN = datetime(2026, 8, 12, 7, 14)


@pytest.fixture
def renderer(config) -> PlateBoardRenderer:
    return PlateBoardRenderer(config)


def sighting(**kwargs) -> Sighting:
    defaults = dict(
        species=Species("eurbla", "Blackbird", "Turdus merula"),
        location="Thorpe",
        observed_at=WHEN,
        count=1,
        notable=False,
    )
    defaults.update(kwargs)
    return Sighting(**defaults)


def test_the_observation_time_is_not_printed(renderer) -> None:
    line = renderer._detail_line(sighting())
    assert "07" not in line and ":" not in line
    assert line == ""


def test_the_head_count_is_not_printed(renderer) -> None:
    line = renderer._detail_line(sighting(count=12))
    assert "12" not in line
    assert "bird" not in line
    assert line == ""


def test_a_notable_sighting_still_says_so(renderer) -> None:
    # The county recorder thought this one worth remarking on, and the
    # board already prefers a notable sighting for the headline.
    assert renderer._detail_line(sighting(notable=True)) == "notable"


def test_a_notable_sighting_says_only_that(renderer) -> None:
    line = renderer._detail_line(sighting(notable=True, count=12))
    assert line == "notable"


def test_the_board_still_renders_with_an_empty_detail_line(renderer, config) -> None:
    """The callers ask before reserving height for the line, so an empty
    one is not a hole in the layout -- the name sits closer to the rule."""
    from birddisplay.model import Board

    board = Board(
        generated_at=datetime.now().astimezone(),
        region_name="Derbyshire",
        headline=sighting(),
        also_seen=[],
        species_count=1,
        checklist_note="",
    )
    image = renderer.render(board, None)
    assert image.size == config.display.board_size
    assert image.mode == "P"


# -- the description under the name -------------------------------------


def board_with(description: str, also_seen=()) -> "Board":
    from birddisplay.model import Board

    return Board(
        generated_at=datetime.now().astimezone(),
        region_name="Derbyshire",
        headline=sighting(),
        also_seen=list(also_seen),
        species_count=1 + len(also_seen),
        headline_description=description,
    )


def test_the_description_is_wrapped_and_capped(renderer) -> None:
    """Past three lines the board reads as a page about a bird rather
    than a picture of one."""
    long_text = "A common and widespread thrush of gardens. " * 12
    lines = renderer._description_lines(board_with(long_text), 408)
    assert 0 < len(lines) <= 3


def test_no_description_reserves_no_room(renderer) -> None:
    # The usual case on an old cache, and on any bird Wikipedia has no
    # article for. The plate grows into the space instead.
    assert renderer._description_lines(board_with(""), 408) == []
    assert renderer._description_lines(board_with("   "), 408) == []


def test_a_described_board_gives_the_plate_less_room(renderer) -> None:
    """The reservation is real: the text block starts higher up."""
    footer_top = 700
    without = renderer._portrait_text_top(board_with(""), footer_top)
    with_text = renderer._portrait_text_top(
        board_with("A thrush of gardens and hedgerows."), footer_top
    )
    assert with_text < without


def test_the_footer_says_other_sightings(renderer) -> None:
    from birddisplay.render.plate_board import FOOTER_LABEL

    assert FOOTER_LABEL == "OTHER SIGHTINGS"


def test_the_footer_shows_at_most_the_configured_count(renderer, config) -> None:
    assert config.board.also_seen_count == 3
    many = [
        Sighting(
            species=Species(f"sp{i}", f"Bird {i}", "Genus species"),
            location="Thorpe",
            observed_at=WHEN,
        )
        for i in range(9)
    ]
    assert len(renderer._also_seen_names(board_with("", many))) == 3


def test_a_described_board_still_renders(renderer, config) -> None:
    image = renderer.render(board_with("A thrush of gardens and hedgerows."), None)
    assert image.size == config.display.board_size
    assert image.mode == "P"
