from __future__ import annotations

import pytest

from birddisplay.config import ConfigError, load_config
from tests.conftest import REPO_ROOT


def test_shipped_config_loads():
    config = load_config(REPO_ROOT / "config.toml")
    assert config.region.code
    assert config.display.width == 800
    assert config.display.height == 480
    assert len(config.palette.as_list()) == 6
    assert len(config.palette.match) == 6


def test_api_key_comes_from_the_environment(monkeypatch):
    config = load_config(REPO_ROOT / "config.toml")
    monkeypatch.setenv(config.ebird.api_key_env, "  abc123  ")
    assert config.ebird.api_key == "abc123"


def test_missing_api_key_is_a_clear_error(monkeypatch):
    config = load_config(REPO_ROOT / "config.toml")
    monkeypatch.delenv(config.ebird.api_key_env, raising=False)
    with pytest.raises(ConfigError, match="keygen"):
        _ = config.ebird.api_key


def _tweaked(tmp_path, key: str, value: str):
    """The shipped config with one key given a new value.

    Matched on the key rather than the whole line: the coordinates and
    region are meant to be re-pointed at wherever the panel hangs, and a
    validation test should not fail just because somebody moved house.
    """
    source = (REPO_ROOT / "config.toml").read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if line.startswith(f"{key} = ")]
    assert matches, f"no {key!r} assignment in config.toml"
    assert len(matches) == 1, f"{key!r} is assigned {len(matches)} times"
    lines[matches[0]] = f"{key} = {value}\n"
    path = tmp_path / "config.toml"
    path.write_text("".join(lines), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "key, value, message",
    [
        ("lat", "200.0", "out of range"),
        ("lng", "-400.0", "out of range"),
        # eBird caps dist at 50km and back at 30 days. Failing at load
        # time beats a 400 from the API at 06:50 on a Sunday.
        ("radius_km", "99", "radius_km"),
        ("back_days", "40", "back_days"),
        ("notable_back_days", "90", "notable_back_days"),
        ("backend", '"hologram"', "backend"),
        ("rotate", "90", "rotate"),
    ],
)
def test_invalid_values_are_rejected_at_load_time(tmp_path, key, value, message):
    with pytest.raises(ConfigError, match=message):
        load_config(_tweaked(tmp_path, key, value))


def test_an_empty_user_agent_is_rejected(tmp_path):
    # Wikimedia's policy requires a descriptive UA with contact info.
    source = (REPO_ROOT / "config.toml").read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    line = next(l for l in source.splitlines() if l.startswith("user_agent ="))
    path.write_text(source.replace(line, 'user_agent = "  "'), encoding="utf-8")
    with pytest.raises(ConfigError, match="user_agent"):
        load_config(path)


def test_a_missing_config_file_says_so(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_dropping_the_match_palette_falls_back_to_the_saturated_defaults(tmp_path):
    from birddisplay.config import NOMINAL_MATCH_RGB

    source = (REPO_ROOT / "config.toml").read_text(encoding="utf-8")
    head, _, _ = source.partition("[palette.match]")
    path = tmp_path / "config.toml"
    path.write_text(head, encoding="utf-8")
    assert load_config(path).palette.match == NOMINAL_MATCH_RGB
