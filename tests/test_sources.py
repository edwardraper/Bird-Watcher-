"""Tests for the network layer, against a fake session.

Nothing here touches the internet. The point is the failure behaviour:
this code runs unattended on a wall, so the interesting cases are the
ones where the other end misbehaves.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from birddisplay.sources.ebird import EbirdClient
from birddisplay.sources.http import FetchError, HttpClient
from birddisplay.sources.images import ImageSource, ImageUnavailable, _clean_html
from birddisplay.sources.taxonomy import Taxonomy, ensure_taxonomy, load_taxonomy


class FakeResponse:
    def __init__(self, status_code=200, payload=None, body=b"", text=""):
        self.status_code = status_code
        self._payload = payload
        self._body = body
        self.text = text or (json.dumps(payload) if payload is not None else "")

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON here")
        return self._payload

    def iter_content(self, chunk_size=8192):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]

    def close(self):
        pass


class FakeSession:
    """Returns queued responses in order, recording every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.requests.append(
            {"url": url, "params": params, "headers": headers, "timeout": timeout}
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    monkeypatch.setattr("birddisplay.sources.http.time.sleep", lambda _: None)


def client_for(config, responses) -> tuple[HttpClient, FakeSession]:
    session = FakeSession(responses)
    return HttpClient(config.http, session=session), session


# -- HTTP ----------------------------------------------------------------


def test_a_transient_server_error_is_retried(config):
    http, session = client_for(
        config, [FakeResponse(503), FakeResponse(200, payload={"ok": True})]
    )
    assert http.get_json("https://example.test/x") == {"ok": True}
    assert len(session.requests) == 2


def test_a_dropped_connection_is_retried(config):
    http, session = client_for(
        config,
        [
            requests.ConnectionError("wifi went away"),
            FakeResponse(200, payload={"ok": True}),
        ],
    )
    assert http.get_json("https://example.test/x") == {"ok": True}
    assert len(session.requests) == 2


def test_retries_are_bounded(config):
    http, session = client_for(config, [FakeResponse(500)] * 3)
    with pytest.raises(FetchError, match="after 3 attempts"):
        http.get_json("https://example.test/x")
    assert len(session.requests) == config.http.retries


def test_a_client_error_fails_immediately(config):
    # A 403 from a bad API key will fail identically on the second try.
    http, session = client_for(config, [FakeResponse(403, text="bad key")])
    with pytest.raises(FetchError, match="403"):
        http.get_json("https://example.test/x")
    assert len(session.requests) == 1


def test_every_request_carries_a_timeout(config):
    http, session = client_for(config, [FakeResponse(200, payload=[])])
    http.get_json("https://example.test/x")
    assert session.requests[0]["timeout"] == config.http.timeout_seconds


def test_the_user_agent_is_set_once_on_the_session(config):
    http, session = client_for(config, [])
    assert session.headers["User-Agent"] == config.http.user_agent


def test_a_non_json_response_is_an_error_not_a_crash(config):
    http, _ = client_for(config, [FakeResponse(200, payload=None, text="<html>")])
    with pytest.raises(FetchError, match="did not return JSON"):
        http.get_json("https://example.test/x")


# -- eBird ---------------------------------------------------------------


def test_the_api_key_travels_in_the_header(config, monkeypatch):
    monkeypatch.setenv(config.ebird.api_key_env, "secret-key")
    http, session = client_for(config, [FakeResponse(200, payload=[])])
    EbirdClient(config, http).recent_nearby()
    assert session.requests[0]["headers"]["X-eBirdApiToken"] == "secret-key"


def test_requests_stay_inside_ebirds_limits(config, monkeypatch):
    monkeypatch.setenv(config.ebird.api_key_env, "k")
    http, session = client_for(config, [FakeResponse(200, payload=[])])
    EbirdClient(config, http).recent_nearby(dist_km=500, back_days=90)
    params = session.requests[0]["params"]
    assert params["dist"] == 50
    assert params["back"] == 30


def test_british_common_names_are_requested(config, monkeypatch):
    monkeypatch.setenv(config.ebird.api_key_env, "k")
    http, session = client_for(config, [FakeResponse(200, payload=[])])
    EbirdClient(config, http).notable()
    assert session.requests[0]["params"]["sppLocale"] == "en_UK"
    assert session.requests[0]["params"]["detail"] == "full"


def test_rare_birds_are_asked_for_by_radius(config, monkeypatch):
    """Both halves of the board must mean the same thing by "near".

    The seen-nearby list is a circle around lat/lng. Asking for rare
    birds by county instead lets a bird ninety kilometres up the Dark
    Peak headline a board about Thorpe.
    """
    monkeypatch.setenv(config.ebird.api_key_env, "k")
    http, session = client_for(config, [FakeResponse(200, payload=[])])
    EbirdClient(config, http).notable()

    request = session.requests[0]
    assert request["url"].endswith("/data/obs/geo/recent/notable")
    assert config.region.code not in request["url"]
    assert request["params"]["lat"] == config.region.lat
    assert request["params"]["lng"] == config.region.lng
    assert request["params"]["dist"] == config.region.radius_km


def test_the_county_wide_query_is_still_available(config, monkeypatch):
    """Turned off, the old behaviour is exactly the old behaviour."""
    from dataclasses import replace

    monkeypatch.setenv(config.ebird.api_key_env, "k")
    county = replace(
        config, region=replace(config.region, notable_within_radius=False)
    )
    http, session = client_for(county, [FakeResponse(200, payload=[])])
    EbirdClient(county, http).notable()

    request = session.requests[0]
    assert county.region.code in request["url"]
    assert "lat" not in request["params"]


def test_an_explicit_region_code_overrides_the_radius(config, monkeypatch):
    monkeypatch.setenv(config.ebird.api_key_env, "k")
    http, session = client_for(config, [FakeResponse(200, payload=[])])
    EbirdClient(config, http).notable(region_code="GB-ENG-STS")
    assert "GB-ENG-STS" in session.requests[0]["url"]


def test_the_radius_is_capped_for_rare_birds_too(config, monkeypatch):
    from dataclasses import replace

    monkeypatch.setenv(config.ebird.api_key_env, "k")
    wide = replace(config, region=replace(config.region, radius_km=500))
    http, session = client_for(wide, [FakeResponse(200, payload=[])])
    EbirdClient(wide, http).notable()
    assert session.requests[0]["params"]["dist"] == 50


# -- taxonomy ------------------------------------------------------------


class FakeEbird:
    def __init__(self, rows, fail=False):
        self.rows = rows
        self.fail = fail
        self.calls = 0

    def taxonomy(self):
        self.calls += 1
        if self.fail:
            raise FetchError("eBird is down")
        return self.rows


TAXONOMY_ROWS = [
    {
        "speciesCode": "eurrob1",
        "comName": "European Robin",
        "sciName": "Erithacus rubecula",
        "category": "species",
        "familyComName": "Old World Flycatchers",
    }
]


def test_the_taxonomy_is_downloaded_once_and_reused(config, tmp_path):
    path = tmp_path / "taxonomy.json"
    client = FakeEbird(TAXONOMY_ROWS)

    first = ensure_taxonomy(client, path, max_age_days=30)
    second = ensure_taxonomy(client, path, max_age_days=30)

    # ~17k rows is not something to fetch three times a day.
    assert client.calls == 1
    assert first.get("eurrob1").common_name == "European Robin"
    assert second.get("eurrob1").common_name == "European Robin"


def test_an_old_taxonomy_is_refreshed(config, tmp_path):
    path = tmp_path / "taxonomy.json"
    stale = Taxonomy.from_ebird(TAXONOMY_ROWS)
    stale.fetched_at = datetime.now(timezone.utc) - timedelta(days=90)
    from birddisplay.cache import write_json_atomic

    write_json_atomic(path, stale.to_dict())

    client = FakeEbird(TAXONOMY_ROWS)
    ensure_taxonomy(client, path, max_age_days=30)
    assert client.calls == 1


def test_a_failed_refresh_keeps_using_the_old_taxonomy(config, tmp_path):
    # Bird names do not go bad. A stale cache beats no board.
    path = tmp_path / "taxonomy.json"
    ensure_taxonomy(FakeEbird(TAXONOMY_ROWS), path, max_age_days=30)

    taxonomy = ensure_taxonomy(FakeEbird([], fail=True), path, max_age_days=0)
    assert taxonomy.get("eurrob1").common_name == "European Robin"


def test_a_failed_first_fetch_has_nothing_to_fall_back_on(config, tmp_path):
    with pytest.raises(FetchError):
        ensure_taxonomy(FakeEbird([], fail=True), tmp_path / "t.json", max_age_days=30)


def test_a_corrupt_taxonomy_cache_is_refetched(config, tmp_path):
    path = tmp_path / "taxonomy.json"
    path.write_text("{ this is not json", encoding="utf-8")
    client = FakeEbird(TAXONOMY_ROWS)
    assert ensure_taxonomy(client, path, max_age_days=30).get("eurrob1")
    assert client.calls == 1
    assert load_taxonomy(path).get("eurrob1")


# -- images --------------------------------------------------------------


SUMMARY = {
    "type": "standard",
    "titles": {"canonical": "Upupa_epops"},
    "originalimage": {
        "source": "https://upload.wikimedia.org/wikipedia/commons/1/12/Hoopoe.jpg"
    },
}

IMAGEINFO = {
    "query": {
        "pages": [
            {
                "title": "File:Hoopoe.jpg",
                "imageinfo": [
                    {
                        "thumburl": "https://upload.wikimedia.org/thumb/Hoopoe.jpg",
                        "url": "https://upload.wikimedia.org/Hoopoe.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Hoopoe.jpg",
                        "extmetadata": {
                            "Artist": {
                                "value": '<a href="//commons.wikimedia.org/wiki/User:X">Jane Doe</a>'
                            },
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        },
                    }
                ],
            }
        ]
    }
}

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"fake jpeg payload" * 8


def species(code="hoopoe"):
    from birddisplay.model import Species

    return Species(code, "Eurasian Hoopoe", "Upupa epops")


def test_a_photo_is_downloaded_with_its_credit(config):
    http, session = client_for(
        config,
        [
            FakeResponse(200, payload=SUMMARY),
            FakeResponse(200, payload=IMAGEINFO),
            FakeResponse(200, body=JPEG_BYTES),
        ],
    )
    photo = ImageSource(config, http).photo_for(species())

    assert photo.credit == "Jane Doe"
    assert photo.licence == "CC BY-SA 4.0"
    assert photo.credit_line == "Jane Doe / CC BY-SA 4.0"
    from pathlib import Path

    assert Path(photo.path).read_bytes() == JPEG_BYTES


def test_the_scientific_name_is_tried_first(config):
    http, session = client_for(
        config,
        [
            FakeResponse(200, payload=SUMMARY),
            FakeResponse(200, payload=IMAGEINFO),
            FakeResponse(200, body=JPEG_BYTES),
        ],
    )
    ImageSource(config, http).photo_for(species())
    assert "Upupa_epops" in session.requests[0]["url"]


def test_a_cached_photo_is_not_downloaded_again(config):
    # There is no reason to re-download a robin.
    http, session = client_for(
        config,
        [
            FakeResponse(200, payload=SUMMARY),
            FakeResponse(200, payload=IMAGEINFO),
            FakeResponse(200, body=JPEG_BYTES),
        ],
    )
    source = ImageSource(config, http)
    first = source.photo_for(species())
    before = len(session.requests)

    second = source.photo_for(species())
    assert len(session.requests) == before
    assert second.path == first.path
    assert second.credit_line == first.credit_line


def test_a_disambiguation_page_is_not_used(config):
    http, _ = client_for(
        config,
        [
            FakeResponse(200, payload={"type": "disambiguation"}),
            FakeResponse(200, payload={"type": "disambiguation"}),
        ],
    )
    with pytest.raises(ImageUnavailable):
        ImageSource(config, http).photo_for(species())


def test_an_article_with_no_lead_image_falls_back_to_the_common_name(config):
    http, session = client_for(
        config,
        [
            FakeResponse(200, payload={"type": "standard", "titles": {}}),
            FakeResponse(200, payload=SUMMARY),
            FakeResponse(200, payload=IMAGEINFO),
            FakeResponse(200, body=JPEG_BYTES),
        ],
    )
    ImageSource(config, http).photo_for(species())
    assert "Eurasian_Hoopoe" in session.requests[1]["url"]


def test_a_failed_download_raises_rather_than_leaving_a_stub(config):
    http, _ = client_for(
        config,
        [
            FakeResponse(200, payload=SUMMARY),
            FakeResponse(200, payload=IMAGEINFO),
            FakeResponse(404),
        ],
    )
    source = ImageSource(config, http)
    with pytest.raises(ImageUnavailable):
        source.photo_for(species())
    assert source.cached("hoopoe") is None


def test_a_missing_artist_still_credits_wikimedia(config):
    no_artist = {
        "query": {"pages": [{"imageinfo": [{"url": "https://x.test/a.jpg", "extmetadata": {}}]}]}
    }
    http, _ = client_for(
        config,
        [
            FakeResponse(200, payload=SUMMARY),
            FakeResponse(200, payload=no_artist),
            FakeResponse(200, body=JPEG_BYTES),
        ],
    )
    assert ImageSource(config, http).photo_for(species()).credit == "Wikimedia Commons"


def test_attribution_html_is_flattened_to_one_line():
    assert _clean_html('<a href="#">Jane  Doe</a>') == "Jane Doe"
    assert _clean_html("<span>A &amp; B</span>\n<i>x</i>") == "A & B x"
    assert _clean_html(None) == ""
