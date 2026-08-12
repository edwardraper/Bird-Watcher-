from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from birddisplay.config import Config, load_config
from birddisplay.sources.taxonomy import Taxonomy

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def config(tmp_path: Path) -> Config:
    """The real config.toml, with the cache redirected into tmp_path.

    Using the shipped config keeps the tests honest about it staying valid.
    """
    base = load_config(REPO_ROOT / "config.toml")
    return replace(base, cache=replace(base.cache, dir=tmp_path / "cache"))


@pytest.fixture
def taxonomy() -> Taxonomy:
    return Taxonomy.from_ebird(load_fixture("ebird_taxonomy"))


@pytest.fixture
def notable_raw() -> list[dict]:
    return load_fixture("ebird_notable")


@pytest.fixture
def recent_raw() -> list[dict]:
    return load_fixture("ebird_recent_nearby")
