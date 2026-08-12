"""eBird API 2.0 client.

Deliberately small. eBird gives this away free and asks people not to
hammer it, so one scheduled fetch makes two observation calls, plus the
taxonomy at most once a month.

Docs: https://documenter.getpostman.com/view/664302/S1ENwy59
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Config
from .http import HttpClient

log = logging.getLogger(__name__)

# eBird's own hard limits. We clamp rather than let the API 400 on us.
MAX_DIST_KM = 50
MAX_BACK_DAYS = 30


class EbirdClient:
    def __init__(self, config: Config, http: HttpClient):
        self.config = config
        self.http = http
        self._auth = {"X-eBirdApiToken": config.ebird.api_key}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.config.ebird.base_url}{path}"
        return self.http.get_json(url, params=params, headers=self._auth)

    def recent_nearby(
        self,
        *,
        lat: float | None = None,
        lng: float | None = None,
        dist_km: int | None = None,
        back_days: int | None = None,
        max_results: int = 200,
    ) -> list[dict[str, Any]]:
        """Recent observations near a point, most recent per species."""
        region = self.config.region
        params = {
            "lat": round(lat if lat is not None else region.lat, 4),
            "lng": round(lng if lng is not None else region.lng, 4),
            "dist": min(dist_km or region.radius_km, MAX_DIST_KM),
            "back": min(back_days or region.back_days, MAX_BACK_DAYS),
            "maxResults": max_results,
            "sppLocale": self.config.ebird.locale,
        }
        data = self._get("/data/obs/geo/recent", params)
        log.info("eBird returned %d recent nearby observations", len(data))
        return data

    def notable(
        self, *, region_code: str | None = None, back_days: int | None = None
    ) -> list[dict[str, Any]]:
        """Notable (locally rare or out of season) sightings.

        By radius when the config asks for it, which is the honest
        pairing: the "seen nearby" list is a circle around lat/lng, and a
        county is not. Derbyshire runs about ninety kilometres north to
        south, so the region form can headline the board with a rare bird
        seen an hour's drive away and call it local.

        The cost is real and worth knowing: a rare bird is rare, and a
        sixteen-kilometre circle will often contain none at all. Then the
        board simply has no rare headline and shows the rotation instead,
        which it already handles.
        """
        region = self.config.region
        back = min(back_days or region.notable_back_days, MAX_BACK_DAYS)
        params: dict[str, Any] = {
            "back": back,
            "detail": "full",
            "sppLocale": self.config.ebird.locale,
        }

        if region_code is None and region.notable_within_radius:
            params.update(
                {
                    "lat": region.lat,
                    "lng": region.lng,
                    "dist": min(region.radius_km, MAX_DIST_KM),
                }
            )
            data = self._get("/data/obs/geo/recent/notable", params)
            log.info(
                "eBird returned %d notable observations within %d km",
                len(data),
                params["dist"],
            )
            return data

        code = region_code or region.code
        data = self._get(f"/data/obs/{code}/recent/notable", params)
        log.info("eBird returned %d notable observations for %s", len(data), code)
        return data

    def taxonomy(self) -> list[dict[str, Any]]:
        """The full ~17k-row taxonomy. Called at most once a month."""
        params = {"fmt": "json", "locale": self.config.ebird.locale}
        data = self._get("/ref/taxonomy/ebird", params)
        log.info("eBird taxonomy: %d rows", len(data))
        return data

    def subnational2_list(self, parent_region: str) -> list[dict[str, Any]]:
        """Region code lookup. Setup-time only -- see scripts/find_region.py."""
        return self._get(f"/ref/region/list/subnational2/{parent_region}")
