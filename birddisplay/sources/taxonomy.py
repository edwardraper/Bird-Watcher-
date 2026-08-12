"""eBird taxonomy: fetched rarely, cached to disk, used to name species.

The full taxonomy is ~17k rows and does not change intra-day (or intra-year,
really). We keep only the four fields the board needs, which turns a ~10MB
download into a ~2MB cache file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..cache import CacheCorrupt, CacheMissing, read_json, write_json_atomic
from ..model import Sighting, Species
from .ebird import EbirdClient

log = logging.getLogger(__name__)

TAXONOMY_SCHEMA_VERSION = 1


@dataclass
class Taxonomy:
    """species code -> names."""

    species: dict[str, Species]
    fetched_at: datetime

    def get(self, code: str) -> Species | None:
        return self.species.get(code)

    def age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        fetched = self.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return (now - fetched).total_seconds() / 86400.0

    def enrich(self, sighting: Sighting) -> Sighting:
        """Upgrade a sighting's names using the taxonomy.

        Observation records already carry comName/sciName, so this is a
        refinement rather than a requirement: it fills in the family and
        guarantees consistent naming across the notable and nearby feeds.
        """
        known = self.get(sighting.species.code)
        if known is None:
            return sighting
        merged = Species(
            code=known.code,
            common_name=known.common_name or sighting.species.common_name,
            scientific_name=known.scientific_name or sighting.species.scientific_name,
            family=known.family,
        )
        return replace(sighting, species=merged)

    def enrich_all(self, sightings: Iterable[Sighting]) -> list[Sighting]:
        return [self.enrich(s) for s in sightings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TAXONOMY_SCHEMA_VERSION,
            "fetched_at": self.fetched_at.isoformat(),
            "species": {code: sp.to_dict() for code, sp in self.species.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Taxonomy:
        if int(data.get("schema_version", 0)) != TAXONOMY_SCHEMA_VERSION:
            raise CacheCorrupt("taxonomy cache is an old schema version")
        return cls(
            species={
                code: Species.from_dict(raw)
                for code, raw in data.get("species", {}).items()
            },
            fetched_at=datetime.fromisoformat(data["fetched_at"]),
        )

    @classmethod
    def from_ebird(cls, rows: list[dict[str, Any]]) -> Taxonomy:
        species: dict[str, Species] = {}
        for row in rows:
            code = row.get("speciesCode")
            if not code:
                continue
            # "issf"/"slash"/"spuh" rows are subspecies and ambiguous
            # groupings -- they clutter the board, so keep true species only.
            if row.get("category") not in (None, "species"):
                continue
            species[code] = Species(
                code=code,
                common_name=row.get("comName", code),
                scientific_name=row.get("sciName", ""),
                family=row.get("familyComName", ""),
            )
        return cls(species=species, fetched_at=datetime.now(timezone.utc))


def load_taxonomy(path: Path) -> Taxonomy:
    data = read_json(path)
    try:
        return Taxonomy.from_dict(data)
    except (KeyError, ValueError, TypeError) as exc:
        raise CacheCorrupt(f"{path} is not a usable taxonomy: {exc}") from exc


def ensure_taxonomy(
    client: EbirdClient, path: Path, max_age_days: int, force: bool = False
) -> Taxonomy:
    """Return a taxonomy, downloading it only if we have to.

    A stale-but-readable cache beats a failed download: bird names do not
    go bad, so if the refresh fails we carry on with what we have.
    """
    cached: Taxonomy | None = None
    if not force:
        try:
            cached = load_taxonomy(path)
        except (CacheMissing, CacheCorrupt) as exc:
            log.info("taxonomy cache unusable (%s); fetching", exc)
        else:
            age = cached.age_days()
            if age <= max_age_days:
                log.debug("taxonomy cache is %.1f days old; reusing", age)
                return cached
            log.info("taxonomy cache is %.1f days old; refreshing", age)

    try:
        taxonomy = Taxonomy.from_ebird(client.taxonomy())
    except Exception as exc:  # noqa: BLE001 - any failure falls back to cache
        if cached is not None:
            log.warning("taxonomy refresh failed (%s); using stale cache", exc)
            return cached
        raise

    write_json_atomic(path, taxonomy.to_dict())
    log.info("cached %d species to %s", len(taxonomy.species), path)
    return taxonomy
