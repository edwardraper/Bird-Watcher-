"""Tests for the setup diagnostic.

This script exists to tell someone what is wrong when the live API does
not behave, so the thing worth testing is that it names the right problem.
"""

from __future__ import annotations

import pytest

from birddisplay.sources.http import FetchError
from scripts import check_api


class StubClient:
    def __init__(self, *, recent=None, notable=None, taxonomy=None, error=None):
        self._recent = recent if recent is not None else []
        self._notable = notable if notable is not None else []
        self._taxonomy = taxonomy if taxonomy is not None else []
        self._error = error

    def recent_nearby(self, **kwargs):
        if self._error:
            raise self._error
        return self._recent

    def notable(self, **kwargs):
        if self._error:
            raise self._error
        return self._notable

    def taxonomy(self):
        if self._error:
            raise self._error
        return self._taxonomy


OBSERVATION = {
    "speciesCode": "eurrob1",
    "comName": "European Robin",
    "sciName": "Erithacus rubecula",
    "locName": "Bowling Green Marsh",
    "obsDt": "2026-08-11 07:14",
}


def statuses(report: check_api.Report) -> dict[str, str]:
    return {name: status for status, name, _ in report.rows}


def details(report: check_api.Report) -> str:
    return " ".join(detail for _, _, detail in report.rows)


def test_a_rejected_key_is_named_as_an_auth_problem(capsys):
    report = check_api.Report()
    check_api.check_auth(StubClient(error=FetchError("HTTP 403: forbidden")), report)
    capsys.readouterr()

    assert statuses(report)["authentication"] == check_api.FAIL
    assert "keygen" in details(report)


def test_an_unknown_region_code_points_at_find_region(config, capsys):
    report = check_api.Report()
    check_api.check_notable(StubClient(error=FetchError("HTTP 404")), config, report)
    capsys.readouterr()

    assert statuses(report)["region code"] == check_api.FAIL
    assert "find_region" in details(report)


def test_no_notable_sightings_is_a_warning_not_a_failure(config, capsys):
    # Perfectly normal. The headline just rotates through recent species.
    report = check_api.Report()
    check_api.check_notable(StubClient(notable=[]), config, report)
    capsys.readouterr()

    assert statuses(report)["notable sightings"] == check_api.WARN
    assert not report.failed


def test_an_empty_region_suggests_widening_the_search(config, capsys):
    report = check_api.Report()
    check_api.check_recent([], config, report)
    capsys.readouterr()

    assert not report.failed
    assert "radius_km" in details(report)


def test_a_changed_response_shape_is_a_failure(config, capsys):
    # The board builder reads these fields directly.
    truncated = {k: v for k, v in OBSERVATION.items() if k != "sciName"}
    report = check_api.Report()
    check_api.check_recent([truncated], config, report)
    capsys.readouterr()

    assert report.failed
    assert "sciName" in details(report)


def test_a_good_response_passes(config, capsys):
    report = check_api.Report()
    check_api.check_recent([OBSERVATION], config, report)
    capsys.readouterr()

    assert not report.failed
    assert "Bowling Green Marsh" in details(report)


def test_american_names_warn_that_the_locale_was_ignored(config, capsys):
    rows = [
        {"speciesCode": "dunnoc1", "comName": "Hedge Accentor"},
        {"speciesCode": "eurrob1", "comName": "European Robin"},
    ] * 6000
    report = check_api.Report()
    check_api.check_taxonomy(StubClient(taxonomy=rows), config, report)
    capsys.readouterr()

    locale_check = next(k for k in statuses(report) if "British" in k)
    assert statuses(report)[locale_check] == check_api.WARN
    assert "Hedge Accentor" in details(report)


def test_british_names_pass(config, capsys):
    rows = [
        {"speciesCode": "dunnoc1", "comName": "Dunnock"},
        {"speciesCode": "eurrob1", "comName": "European Robin"},
    ] * 6000
    report = check_api.Report()
    check_api.check_taxonomy(StubClient(taxonomy=rows), config, report)
    capsys.readouterr()

    assert not report.failed


def test_a_missing_key_exits_two_without_touching_the_network(monkeypatch, capsys):
    monkeypatch.delenv("EBIRD_API_KEY", raising=False)
    monkeypatch.setattr(
        "requests.Session.get",
        lambda *a, **k: pytest.fail("should not reach the network"),
    )
    assert check_api.main(["--skip-images"]) == 2
