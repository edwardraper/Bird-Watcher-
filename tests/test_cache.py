from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from birddisplay.cache import (
    CacheCorrupt,
    CacheMissing,
    load_board,
    load_state,
    record_featured,
    save_board,
    save_state,
    write_json_atomic,
)
from birddisplay.model import Board, Photo, Sighting, Species


def make_board(**kwargs) -> Board:
    defaults = dict(
        generated_at=datetime.now(timezone.utc),
        region_name="Devon",
        headline=Sighting(
            species=Species("hoopoe", "Eurasian Hoopoe", "Upupa epops", "Hoopoes"),
            location="Prawle Point",
            observed_at=datetime(2026, 8, 11, 8, 40),
            count=1,
            notable=True,
        ),
        headline_photo=Photo(path="/tmp/x.jpg", credit="Someone", licence="CC BY-SA 4.0"),
        also_seen=[
            Sighting(
                species=Species("eurrob1", "European Robin", "Erithacus rubecula"),
                location="Killerton",
                observed_at=datetime(2026, 8, 10, 7, 0),
            )
        ],
        species_count=11,
    )
    defaults.update(kwargs)
    return Board(**defaults)


def test_board_survives_a_round_trip(tmp_path):
    path = tmp_path / "cache.json"
    original = make_board()
    save_board(path, original)
    restored = load_board(path)

    assert restored.region_name == original.region_name
    assert restored.headline.species.common_name == "Eurasian Hoopoe"
    assert restored.headline.notable is True
    assert restored.headline_photo.credit_line == "Someone / CC BY-SA 4.0"
    assert [s.species.code for s in restored.also_seen] == ["eurrob1"]
    assert restored.species_count == 11


def test_missing_cache_raises_cache_missing(tmp_path):
    with pytest.raises(CacheMissing):
        load_board(tmp_path / "nope.json")


def test_truncated_cache_raises_rather_than_returning_junk(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text('{"schema_version": 1, "generated_at":', encoding="utf-8")
    with pytest.raises(CacheCorrupt):
        load_board(path)


def test_wrong_schema_version_is_rejected(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(CacheCorrupt):
        load_board(path)


def test_a_failed_write_leaves_the_previous_cache_intact(tmp_path):
    path = tmp_path / "cache.json"
    save_board(path, make_board(region_name="Devon"))

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(path, {"bad": Unserialisable()})

    # The panel keeps yesterday's board rather than showing nothing.
    assert load_board(path).region_name == "Devon"
    assert not list(tmp_path.glob("*.tmp"))


def test_staleness_is_measured_against_the_configured_limit():
    fresh = make_board(generated_at=datetime.now(timezone.utc) - timedelta(hours=6))
    old = make_board(generated_at=datetime.now(timezone.utc) - timedelta(hours=60))
    assert fresh.is_stale(48) is False
    assert old.is_stale(48) is True
    assert old.age_hours() == pytest.approx(60, abs=0.1)


def test_naive_timestamps_are_treated_as_utc():
    naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    board = make_board(generated_at=naive)
    assert board.age_hours() == pytest.approx(2, abs=0.1)


def test_featured_history_rotates_and_is_capped(tmp_path):
    state = load_state(tmp_path / "state.json")
    assert state["featured"] == []

    for code in ("a", "b", "c", "d"):
        record_featured(state, code, memory=3)
    assert state["featured"] == ["d", "c", "b"]

    # Re-featuring an existing species moves it to the front, not a dupe.
    record_featured(state, "c", memory=3)
    assert state["featured"] == ["c", "d", "b"]

    save_state(tmp_path / "state.json", state)
    assert load_state(tmp_path / "state.json")["featured"] == ["c", "d", "b"]


def test_unreadable_state_does_not_stop_the_fetcher(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json at all", encoding="utf-8")
    assert load_state(path) == {"featured": []}
