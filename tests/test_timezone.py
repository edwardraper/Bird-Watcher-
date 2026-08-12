"""The board is dated where it hangs, not where it is rendered.

This only became a bug when rendering moved off the Pi. A GitHub runner is
on UTC; eBird reports British local time. Print one over the other and for
an hour either side of midnight in summer the board carries yesterday's
date above this morning's sightings.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from birddisplay.config import Config
from birddisplay.model import Board, Photo, Sighting, Species
from birddisplay.render.layout import BoardRenderer
from birddisplay.render.plate_board import PlateBoardRenderer

# 00:30 on the 13th in London, 23:30 on the 12th in UTC. British Summer
# Time, so the two disagree about what day it is.
MIDNIGHT_UTC = datetime(2026, 8, 12, 23, 30, tzinfo=timezone.utc)


@pytest.fixture
def board() -> Board:
    species = Species(
        code="eurrob1", common_name="Robin", scientific_name="Erithacus rubecula"
    )
    return Board(
        generated_at=MIDNIGHT_UTC,
        region_name="Derbyshire",
        headline=Sighting(
            species=species,
            location="Thorpe",
            observed_at=datetime(2026, 8, 12, 7, 14),
            count=1,
        ),
        headline_photo=Photo(path="", credit="J. G. Keulemans"),
        also_seen=[],
        species_count=1,
    )


def in_zone(config: Config, name: str) -> Config:
    return replace(config, region=replace(config.region, timezone=name))


def test_the_configured_zone_resolves(config: Config) -> None:
    assert config.region.timezone == "Europe/London"
    assert config.region.tzinfo is not None
    london = MIDNIGHT_UTC.astimezone(config.region.tzinfo)
    assert (london.day, london.hour) == (13, 0)


@pytest.mark.parametrize(
    "renderer_class", [BoardRenderer, PlateBoardRenderer], ids=["photo", "plate"]
)
def test_a_utc_instant_is_drawn_in_local_time(config: Config, board, renderer_class):
    """Hand either renderer a UTC timestamp and it should still draw a
    British board -- the same pixels as if it had been given the London
    time, and different pixels from a board dated in UTC."""
    args = (board, None) if renderer_class is PlateBoardRenderer else (board,)

    london = renderer_class(config).render(*args, MIDNIGHT_UTC)
    utc = renderer_class(in_zone(config, "UTC")).render(*args, MIDNIGHT_UTC)
    already_local = renderer_class(config).render(
        *args, MIDNIGHT_UTC.astimezone(config.region.tzinfo)
    )

    assert london.tobytes() == already_local.tobytes(), "the instant was not converted"
    assert london.tobytes() != utc.tobytes(), "the zone made no difference"


def test_a_naive_time_is_left_alone(config: Config) -> None:
    """The Pi's own clock is already local, so a naive time is already the
    answer. Passing one to .astimezone() would quietly reinterpret it as
    the renderer's own zone and shift it."""
    naive = datetime(2026, 8, 12, 23, 30)
    assert BoardRenderer(config)._local(naive) == naive
    assert BoardRenderer(in_zone(config, "Pacific/Auckland"))._local(naive) == naive


def test_the_generated_at_stamp_is_local_too(config: Config, board) -> None:
    """The footer prints when the cache was written, and that timestamp is
    aware UTC -- so it needs converting even when nothing else does."""
    london = BoardRenderer(config).render(board, MIDNIGHT_UTC)
    utc = BoardRenderer(in_zone(config, "UTC")).render(board, MIDNIGHT_UTC)
    assert london.tobytes() != utc.tobytes()


def test_an_unknown_zone_falls_back_rather_than_failing(config: Config, board) -> None:
    """A missing tz database is not worth a blank wall."""
    broken = in_zone(config, "Middle/Earth")
    assert broken.region.tzinfo is None
    assert BoardRenderer(broken).render(board, MIDNIGHT_UTC) is not None
