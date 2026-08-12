from __future__ import annotations

from datetime import datetime

from birddisplay.fetch import build_board, dedupe_by_species, rank_candidates
from birddisplay.model import Sighting, Species


def sighting(code: str, when: datetime | None, notable: bool = False) -> Sighting:
    return Sighting(
        species=Species(code, code.title(), f"Genus {code}"),
        location="Somewhere",
        observed_at=when,
        notable=notable,
    )


def test_taxonomy_supplies_british_names_and_families(taxonomy):
    raw = Sighting.from_ebird(
        {
            "speciesCode": "dunnoc1",
            "comName": "Hedge Accentor",
            "sciName": "Prunella modularis",
            "locName": "Ludwell Valley Park",
            "obsDt": "2026-08-08 16:45",
        }
    )
    enriched = taxonomy.enrich(raw)
    assert enriched.species.common_name == "Dunnock"
    assert enriched.species.family == "Accentors"
    assert enriched.location == "Ludwell Valley Park"


def test_taxonomy_drops_subspecies_and_slash_entries(taxonomy):
    # "issf" and "slash" rows would put "European Robin (British)" and
    # "Herring Gull/Yellow-legged Gull" on the wall.
    assert "eurrob1" in taxonomy.species
    assert "eurrob2" not in taxonomy.species
    assert "y00423" not in taxonomy.species


def test_unknown_species_keeps_the_names_the_observation_carried(taxonomy):
    raw = Sighting.from_ebird(
        {"speciesCode": "nosuch", "comName": "Mystery Bird", "sciName": "Genus species"}
    )
    assert taxonomy.enrich(raw).species.common_name == "Mystery Bird"


def test_dedupe_keeps_the_most_recent_record_per_species():
    deduped = dedupe_by_species(
        [
            sighting("eurrob1", datetime(2026, 8, 8, 8, 0)),
            sighting("eurrob1", datetime(2026, 8, 11, 7, 14)),
            sighting("comswi", datetime(2026, 8, 9, 19, 40)),
        ]
    )
    by_code = {s.species.code: s for s in deduped}
    assert len(deduped) == 2
    assert by_code["eurrob1"].observed_at == datetime(2026, 8, 11, 7, 14)


def test_dedupe_keeps_the_notable_flag_from_either_feed():
    deduped = dedupe_by_species(
        [
            sighting("hoopoe", datetime(2026, 8, 11, 8, 40), notable=True),
            sighting("hoopoe", datetime(2026, 8, 11, 9, 0), notable=False),
        ]
    )
    assert len(deduped) == 1
    assert deduped[0].notable is True
    assert deduped[0].observed_at == datetime(2026, 8, 11, 9, 0)


def test_observations_without_a_species_code_are_dropped():
    assert dedupe_by_species([sighting("", datetime(2026, 8, 11))]) == []


def test_a_rare_bird_outranks_a_common_one(config):
    notable = [sighting("hoopoe", datetime(2026, 8, 11, 8, 40), notable=True)]
    recent = [sighting("eurrob1", datetime(2026, 8, 11, 9, 0))]
    ranked = rank_candidates(notable, recent, featured=[], config=config)
    assert ranked[0].species.code == "hoopoe"


def test_recently_featured_species_go_to_the_back(config):
    # Otherwise the wall shows a woodpigeon three days running.
    notable = [sighting("hoopoe", datetime(2026, 8, 11), notable=True)]
    recent = [sighting("eurrob1", datetime(2026, 8, 10))]
    ranked = rank_candidates(notable, recent, featured=["hoopoe"], config=config)
    assert ranked[0].species.code == "eurrob1"
    assert ranked[-1].species.code == "hoopoe"


def test_everything_featured_falls_back_to_least_recently_shown(config):
    recent = [sighting("a", datetime(2026, 8, 10)), sighting("b", datetime(2026, 8, 9))]
    # featured is newest-first, so "b" was shown longer ago than "a".
    ranked = rank_candidates([], recent, featured=["a", "b"], config=config)
    assert [s.species.code for s in ranked] == ["b", "a"]


def test_ranking_is_stable_for_a_given_day(config):
    recent = [sighting(code, datetime(2026, 8, 10)) for code in "abcdefgh"]
    first = rank_candidates([], recent, [], config, seed="2026-08-11")
    second = rank_candidates([], recent, [], config, seed="2026-08-11")
    third = rank_candidates([], recent, [], config, seed="2026-08-12")
    assert [s.species.code for s in first] == [s.species.code for s in second]
    assert [s.species.code for s in first] != [s.species.code for s in third]


def test_build_board_produces_a_renderable_payload(
    config, taxonomy, notable_raw, recent_raw
):
    board, headline = build_board(
        config, taxonomy, notable_raw, recent_raw, images=None, featured=[]
    )
    assert headline is not None
    assert board.headline.species.common_name == "Eurasian Hoopoe"
    assert board.headline.notable is True
    # The headline must not also appear in the list underneath itself.
    assert board.headline.species.code not in {s.species.code for s in board.also_seen}
    # 12 raw records, one of which is a duplicate robin.
    assert board.species_count == 11
    assert "30 km" in board.checklist_note


def test_build_board_without_an_image_source_still_builds(
    config, taxonomy, notable_raw, recent_raw
):
    board, _ = build_board(
        config, taxonomy, notable_raw, recent_raw, images=None, featured=[]
    )
    assert board.headline_photo is None


def test_build_board_falls_through_to_a_species_it_can_illustrate(
    config, taxonomy, notable_raw, recent_raw
):
    from birddisplay.model import Photo
    from birddisplay.sources.images import ImageUnavailable

    class NoHoopoePictures:
        """Everything is illustrated except the top-ranked bird."""

        def photo_for(self, species, refresh=False):
            if species.code == "hoopoe":
                raise ImageUnavailable("no image")
            return Photo(path="/tmp/bird.jpg", credit="A Photographer")

    board, headline = build_board(
        config, taxonomy, notable_raw, recent_raw, NoHoopoePictures(), featured=[]
    )
    assert headline.species.code != "hoopoe"
    assert board.headline_photo.credit == "A Photographer"
    assert board.headline.species.code not in {s.species.code for s in board.also_seen}


def test_the_image_fallback_gives_up_rather_than_hammering_wikimedia(
    config, taxonomy, notable_raw, recent_raw
):
    from birddisplay.fetch import MAX_IMAGE_ATTEMPTS
    from birddisplay.sources.images import ImageUnavailable

    attempts = []

    class NothingWorks:
        def photo_for(self, species, refresh=False):
            attempts.append(species.code)
            raise ImageUnavailable("no image")

    build_board(config, taxonomy, notable_raw, recent_raw, NothingWorks(), featured=[])
    assert len(attempts) == MAX_IMAGE_ATTEMPTS


def test_no_image_anywhere_still_yields_a_board(
    config, taxonomy, notable_raw, recent_raw
):
    from birddisplay.sources.images import ImageUnavailable

    class NothingWorks:
        def photo_for(self, species, refresh=False):
            raise ImageUnavailable("nope")

    board, headline = build_board(
        config, taxonomy, notable_raw, recent_raw, NothingWorks(), featured=[]
    )
    # Text-only beats no board at all.
    assert headline is not None
    assert board.headline_photo is None
