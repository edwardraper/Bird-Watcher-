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
