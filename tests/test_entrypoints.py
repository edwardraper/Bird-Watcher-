"""End-to-end tests of the two entrypoints, with the network faked out.

The property that matters most: a fetch failure must never blank the
panel. The renderer reads the cache and nothing else, so a board stays on
the wall through an outage.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from birddisplay import fetch as fetch_module
from birddisplay import show as show_module
from birddisplay.cache import load_board, save_board
from birddisplay.model import Board, Sighting, Species
from birddisplay.sources.http import FetchError
from birddisplay.sources.images import ImageUnavailable
from tests.conftest import load_fixture


@pytest.fixture
def wired(config, monkeypatch):
    """fetch.run() with eBird, Wikimedia and the config file all faked."""

    class FakeEbirdClient:
        instances: list["FakeEbirdClient"] = []

        def __init__(self, cfg, http, notable=None, recent=None):
            self.notable_payload = load_fixture("ebird_notable")
            self.recent_payload = load_fixture("ebird_recent_nearby")
            self.notable_error: Exception | None = None
            self.recent_error: Exception | None = None
            FakeEbirdClient.instances.append(self)

        def notable(self, **kwargs):
            if self.notable_error:
                raise self.notable_error
            return self.notable_payload

        def recent_nearby(self, **kwargs):
            if self.recent_error:
                raise self.recent_error
            return self.recent_payload

        def taxonomy(self):
            return load_fixture("ebird_taxonomy")

    FakeEbirdClient.instances.clear()
    monkeypatch.setattr(fetch_module, "EbirdClient", FakeEbirdClient)
    monkeypatch.setattr(fetch_module, "load_config", lambda path=None: config)
    monkeypatch.setattr(show_module, "load_config", lambda path=None: config)

    class NoImages:
        def __init__(self, cfg, http):
            pass

        def photo_for(self, species, refresh=False):
            raise ImageUnavailable("offline in tests")

    monkeypatch.setattr(fetch_module, "ImageSource", NoImages)
    return FakeEbirdClient


def test_fetch_writes_a_cache_the_renderer_can_read(config, wired):
    assert fetch_module.run(config) == 0

    board = load_board(config.cache.board_path)
    # From the shipped config, whatever it currently points at.
    assert board.region_name == config.region.name
    assert board.headline.species.common_name == "Eurasian Hoopoe"
    assert board.species_count == 11


def test_fetch_remembers_what_it_featured(config, wired):
    fetch_module.run(config)
    state = json.loads(config.cache.state_path.read_text(encoding="utf-8"))
    assert state["featured"] == ["hoopoe"]

    # A second run must not pick the same bird again.
    fetch_module.run(config)
    state = json.loads(config.cache.state_path.read_text(encoding="utf-8"))
    assert state["featured"][0] != "hoopoe"
    assert len(state["featured"]) == 2


def test_dry_run_prints_the_payload_and_writes_nothing(config, wired, capsys):
    assert fetch_module.run(config, dry_run=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["headline"]["species"]["common_name"] == "Eurasian Hoopoe"
    assert not config.cache.board_path.exists()


def test_losing_the_notable_feed_still_produces_a_board(config, wired, monkeypatch):
    # A board with no rare bird on it is still a board.
    def failing_notable(cfg, http):
        client = wired(cfg, http)
        client.notable_error = FetchError("eBird 503")
        return client

    monkeypatch.setattr(fetch_module, "EbirdClient", failing_notable)
    assert fetch_module.run(config) == 0

    board = load_board(config.cache.board_path)
    assert board.headline is not None
    assert board.headline.notable is False


def test_a_fetch_failure_leaves_the_previous_cache_alone(config, wired, monkeypatch):
    fetch_module.run(config)
    before = config.cache.board_path.read_text(encoding="utf-8")

    def exploding_client(cfg, http):
        client = wired(cfg, http)
        client.recent_error = FetchError("the wifi is down")
        return client

    monkeypatch.setattr(fetch_module, "EbirdClient", exploding_client)
    # Non-zero so systemctl shows the failure...
    assert fetch_module.main([]) == 1
    # ...but the wall still has yesterday's board.
    assert config.cache.board_path.read_text(encoding="utf-8") == before


def test_an_unexpected_crash_is_caught_rather_than_traced_to_stderr(
    config, wired, monkeypatch
):
    def exploding_client(cfg, http):
        raise ZeroDivisionError("something absurd")

    monkeypatch.setattr(fetch_module, "EbirdClient", exploding_client)
    assert fetch_module.main([]) == 1


def test_a_missing_api_key_exits_two(config, wired, monkeypatch):
    def needs_a_key(cfg, http):
        _ = cfg.ebird.api_key
        raise AssertionError("unreachable")

    monkeypatch.delenv(config.ebird.api_key_env, raising=False)
    monkeypatch.setattr(fetch_module, "EbirdClient", needs_a_key)
    assert fetch_module.main([]) == 2


# -- show ----------------------------------------------------------------


def make_board(**kwargs) -> Board:
    defaults = dict(
        generated_at=datetime.now(timezone.utc),
        region_name="Devon",
        headline=Sighting(
            species=Species("hoopoe", "Eurasian Hoopoe", "Upupa epops"),
            location="Prawle Point",
            observed_at=datetime(2026, 8, 11, 8, 40),
            notable=True,
        ),
        also_seen=[],
        species_count=3,
    )
    defaults.update(kwargs)
    return Board(**defaults)


def test_show_renders_the_cache_to_a_png(config, wired, tmp_path):
    save_board(config.cache.board_path, make_board())
    out = tmp_path / "preview.png"

    assert show_module.main(["--preview", "--preview-path", str(out)]) == 0
    with Image.open(out) as image:
        assert image.size == (800, 480)


def test_show_draws_a_placeholder_when_there_is_no_cache(config, wired, tmp_path):
    # First boot, before the fetcher has ever run.
    out = tmp_path / "preview.png"
    assert show_module.main(["--preview", "--preview-path", str(out)]) == 0
    assert out.exists()


def test_show_draws_a_placeholder_when_the_cache_is_corrupt(config, wired, tmp_path):
    config.cache.board_path.parent.mkdir(parents=True, exist_ok=True)
    config.cache.board_path.write_text("{ truncated", encoding="utf-8")
    out = tmp_path / "preview.png"
    assert show_module.main(["--preview", "--preview-path", str(out)]) == 0
    assert out.exists()


def test_show_still_draws_an_old_board_rather_than_nothing(config, wired, tmp_path):
    save_board(
        config.cache.board_path,
        make_board(generated_at=datetime.now(timezone.utc) - timedelta(days=5)),
    )
    out = tmp_path / "preview.png"
    assert show_module.main(["--preview", "--preview-path", str(out)]) == 0
    assert out.exists()


def test_show_never_talks_to_the_network(config, wired, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the renderer must not make network calls")

    monkeypatch.setattr("requests.Session.get", forbidden)
    save_board(config.cache.board_path, make_board())
    out = tmp_path / "preview.png"
    assert show_module.main(["--preview", "--preview-path", str(out)]) == 0
