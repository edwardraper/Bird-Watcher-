"""Dataclasses for everything that crosses the fetch/render boundary.

These are the only shapes the renderer knows about. The eBird and Wikimedia
JSON never gets further than birddisplay.sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1


def _parse_dt(raw: str | None) -> datetime | None:
    """Parse an eBird observation timestamp.

    eBird returns local time with no offset, as "2026-08-11 07:14" or
    "2026-08-11" for a date-only checklist. We keep it naive: the panel
    shows local time and the Pi's clock is local too.
    """
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class Species:
    """A species, named the way eBird's taxonomy names it."""

    code: str
    common_name: str
    scientific_name: str
    family: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "common_name": self.common_name,
            "scientific_name": self.scientific_name,
            "family": self.family,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Species:
        return cls(
            code=data["code"],
            common_name=data.get("common_name", data["code"]),
            scientific_name=data.get("scientific_name", ""),
            family=data.get("family", ""),
        )


@dataclass(frozen=True)
class Sighting:
    """One observation of one species at one place."""

    species: Species
    location: str
    observed_at: datetime | None
    count: int | None = None
    notable: bool = False
    lat: float | None = None
    lng: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "species": self.species.to_dict(),
            "location": self.location,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "count": self.count,
            "notable": self.notable,
            "lat": self.lat,
            "lng": self.lng,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Sighting:
        raw_dt = data.get("observed_at")
        return cls(
            species=Species.from_dict(data["species"]),
            location=data.get("location", ""),
            observed_at=datetime.fromisoformat(raw_dt) if raw_dt else None,
            count=data.get("count"),
            notable=bool(data.get("notable", False)),
            lat=data.get("lat"),
            lng=data.get("lng"),
        )

    @classmethod
    def from_ebird(cls, obs: dict[str, Any], notable: bool = False) -> Sighting:
        """Build a Sighting from one eBird observation record.

        The record carries comName/sciName already, so this works even
        before the taxonomy cache is populated; taxonomy.enrich() upgrades
        the names afterwards.
        """
        count = obs.get("howMany")
        return cls(
            species=Species(
                code=obs.get("speciesCode", ""),
                common_name=obs.get("comName", obs.get("speciesCode", "")),
                scientific_name=obs.get("sciName", ""),
            ),
            location=obs.get("locName", ""),
            observed_at=_parse_dt(obs.get("obsDt")),
            count=int(count) if isinstance(count, (int, float)) else None,
            notable=notable,
            lat=obs.get("lat"),
            lng=obs.get("lng"),
        )


@dataclass(frozen=True)
class Photo:
    """A cached local image plus the credit line its licence requires."""

    path: str
    credit: str
    licence: str = ""
    source_url: str = ""

    @property
    def credit_line(self) -> str:
        bits = [b for b in (self.credit, self.licence) if b]
        return " / ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "credit": self.credit,
            "licence": self.licence,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Photo:
        return cls(
            path=data["path"],
            credit=data.get("credit", ""),
            licence=data.get("licence", ""),
            source_url=data.get("source_url", ""),
        )


@dataclass
class Board:
    """Everything the renderer needs for one refresh. This is the cache."""

    generated_at: datetime
    region_name: str
    headline: Sighting | None = None
    headline_photo: Photo | None = None
    also_seen: list[Sighting] = field(default_factory=list)
    species_count: int = 0
    checklist_note: str = ""
    schema_version: int = SCHEMA_VERSION

    def age_hours(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        generated = self.generated_at
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        return (now - generated).total_seconds() / 3600.0

    def is_stale(self, max_age_hours: float, now: datetime | None = None) -> bool:
        return self.age_hours(now) > max_age_hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "region_name": self.region_name,
            "headline": self.headline.to_dict() if self.headline else None,
            "headline_photo": (
                self.headline_photo.to_dict() if self.headline_photo else None
            ),
            "also_seen": [s.to_dict() for s in self.also_seen],
            "species_count": self.species_count,
            "checklist_note": self.checklist_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Board:
        version = int(data.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"cache schema version {version}, expected {SCHEMA_VERSION}"
            )
        headline = data.get("headline")
        photo = data.get("headline_photo")
        return cls(
            generated_at=datetime.fromisoformat(data["generated_at"]),
            region_name=data.get("region_name", ""),
            headline=Sighting.from_dict(headline) if headline else None,
            headline_photo=Photo.from_dict(photo) if photo else None,
            also_seen=[Sighting.from_dict(s) for s in data.get("also_seen", [])],
            species_count=int(data.get("species_count", 0)),
            checklist_note=data.get("checklist_note", ""),
            schema_version=version,
        )
