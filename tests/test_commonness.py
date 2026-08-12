"""Ordering the other sightings by rarity, and the sentence under the name.

Rarity is the commonness list read backwards: rank 1 is the most ordinary
bird there is, so the highest rank in a set is the one worth naming. A
bird missing from the list is rarer than anything on it, because the list
is a list of common birds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from birddisplay.model import Sighting, Species
from birddisplay.sources.commonness import UNRANKED, Commonness
from birddisplay.sources.images import _first_sentences

REPO_ROOT = Path(__file__).resolve().parent.parent


def sighting(name: str, scientific: str = "") -> Sighting:
    return Sighting(
        species=Species(name.lower(), name, scientific),
        location="Thorpe",
        observed_at=None,
    )


@pytest.fixture
def commonness() -> Commonness:
    """The real shipped list, so the tests notice if it changes shape."""
    return Commonness.load(REPO_ROOT / "data" / "uk_birds_top200.json")


def test_the_shipped_list_loads(commonness) -> None:
    assert commonness
    assert commonness.rank_of(Species("eurbla", "Blackbird", "Turdus merula")) == 1


def test_a_bird_is_found_by_either_name(commonness) -> None:
    by_common = commonness.rank_of(Species("x", "Blackbird", ""))
    by_scientific = commonness.rank_of(Species("x", "", "Turdus merula"))
    assert by_common == by_scientific == 1


def test_an_unlisted_bird_counts_as_the_rarest_thing_there_is(commonness) -> None:
    # Falling off the end of a list of common birds means uncommon.
    assert commonness.rank_of(Species("x", "Hoopoe", "Upupa epops")) == UNRANKED


def test_the_rarest_bird_comes_first(commonness) -> None:
    # Woodpigeon is rank 2; a yellowhammer is far down the list.
    ordered = commonness.rarest_first(
        [sighting("Woodpigeon"), sighting("Yellowhammer"), sighting("Blackbird")]
    )
    assert [s.species.common_name for s in ordered] == [
        "Yellowhammer",
        "Woodpigeon",
        "Blackbird",
    ]


def test_an_unlisted_bird_outranks_every_listed_one(commonness) -> None:
    ordered = commonness.rarest_first([sighting("Blackbird"), sighting("Hoopoe")])
    assert ordered[0].species.common_name == "Hoopoe"


def test_equally_ranked_birds_keep_the_order_they_arrived_in(commonness) -> None:
    """Stable, so the incoming recency order survives as a tie-break."""
    birds = [sighting("Hoopoe"), sighting("Wryneck"), sighting("Serin")]
    assert [s.species.common_name for s in commonness.rarest_first(birds)] == [
        "Hoopoe",
        "Wryneck",
        "Serin",
    ]


def test_a_missing_list_orders_nothing_rather_than_failing(tmp_path: Path) -> None:
    """Never worth a blank wall: without the list the board still renders,
    it just keeps the recency order it already had."""
    absent = Commonness.load(tmp_path / "nope.json")
    assert not absent
    assert absent.rank_of(Species("x", "Blackbird", "")) == UNRANKED


def test_a_malformed_list_is_declined_quietly(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ truncated", encoding="utf-8")
    assert not Commonness.load(broken)

    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps({"birds": []}), encoding="utf-8")
    assert not Commonness.load(wrong_shape)


def test_a_row_with_no_usable_rank_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "part.json"
    path.write_text(
        json.dumps(
            {
                "species": [
                    {"rank": "not a number", "common_name": "Broken"},
                    {"rank": 7, "common_name": "Fine"},
                ]
            }
        ),
        encoding="utf-8",
    )
    listed = Commonness.load(path)
    assert listed.rank_of(Species("x", "Fine", "")) == 7
    assert listed.rank_of(Species("x", "Broken", "")) == UNRANKED


# -- the description ----------------------------------------------------


def test_whole_sentences_only() -> None:
    text = "One short sentence. A second one here. A third that will not fit."
    assert _first_sentences(text, 45) == "One short sentence. A second one here."


def test_a_first_sentence_too_long_yields_nothing() -> None:
    """A clause cut in half reads as a bug, not as brevity, so the board
    shows the name alone instead."""
    assert _first_sentences("A very long opening sentence indeed.", 10) == ""


def test_an_abbreviation_does_not_end_a_sentence() -> None:
    # "c." and a binomial authority must not split the line.
    text = "The blackbird, Turdus merula L., is c. 25 cm long. It sings."
    assert _first_sentences(text, 200).startswith("The blackbird, Turdus merula L., is")


def test_empty_input_is_empty_output() -> None:
    assert _first_sentences("", 100) == ""
    assert _first_sentences("   ", 100) == ""
