"""Read plates out of the Keulemans database. This is the Pi's side.

The database is built on a laptop by scripts/build_plate_db.py and shipped
in the repository, so this module never touches the network, never needs
numpy or scipy, and works with the wifi off. It opens the file read-only
and answers one question: is there a plate for this bird?

Lookup goes by eBird species code first, because that is the key both ends
agree on. Scientific and common names are fallbacks for the case where the
database was built against a different taxonomy revision than the one the
fetcher is seeing today -- eBird moves birds between genera, and a plate
that cannot be found is worse than a plate found by its old name.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..model import Photo, Species

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_COLUMNS = (
    "species_code, common_name, scientific_name, family, artist, attribution,"
    " work, year, licence, source_url, commons_file, image_format, width,"
    " height, image"
)


@dataclass(frozen=True)
class Plate:
    """One bird, cut off its paper, plus who to credit for it."""

    species_code: str
    common_name: str
    scientific_name: str
    family: str
    artist: str
    attribution: str
    work: str
    year: str
    licence: str
    source_url: str
    commons_file: str
    image_format: str
    width: int
    height: int
    image: bytes

    @property
    def credit_line(self) -> str:
        return " / ".join(bit for bit in (self.artist, self.licence) if bit)

    def write_image(self, directory: Path) -> Path:
        """Drop the image on disk so anything taking a path can read it.

        Written atomically: the renderer may be reading the previous copy.
        """
        directory.mkdir(parents=True, exist_ok=True)
        dest = directory / f"{self.species_code}.{self.image_format}"
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(self.image)
        tmp.replace(dest)
        return dest

    def as_photo(self, directory: Path) -> Photo:
        """The Photo record the board already knows how to render."""
        return Photo(
            path=str(self.write_image(directory)),
            credit=self.artist,
            licence=self.licence,
            source_url=self.source_url,
        )


class PlateStore:
    """Read-only access to the plate database."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser()
        self._connection = sqlite3.connect(
            f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row

    # -- construction ----------------------------------------------------

    @classmethod
    def maybe(cls, path: Path | str | None) -> PlateStore | None:
        """Open the store, or return None if it is not usable.

        A missing or corrupt plate file must never take the board down --
        the display falls back to photographs, or to text.
        """
        if not path:
            return None
        try:
            store = cls(path)
        except sqlite3.Error as exc:
            log.warning("plate database %s is unusable: %s", path, exc)
            return None
        version = store.meta("schema_version")
        if version and version != str(SCHEMA_VERSION):
            log.warning(
                "plate database is schema %s, expected %s; ignoring it",
                version,
                SCHEMA_VERSION,
            )
            store.close()
            return None
        return store

    # -- queries ---------------------------------------------------------

    def meta(self, key: str) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.Error as exc:
            log.warning("plate database has no usable meta table: %s", exc)
            return None
        return row["value"] if row else None

    def count(self) -> int:
        try:
            row = self._connection.execute("SELECT COUNT(*) AS n FROM plates").fetchone()
        except sqlite3.Error:
            return 0
        return int(row["n"])

    def _row(self, sql: str, *params: object) -> Plate | None:
        try:
            row = self._connection.execute(
                f"SELECT {_COLUMNS} FROM plates WHERE {sql}", params
            ).fetchone()
        except sqlite3.Error as exc:
            log.warning("plate lookup failed: %s", exc)
            return None
        if row is None:
            return None
        return Plate(
            species_code=row["species_code"],
            common_name=row["common_name"],
            scientific_name=row["scientific_name"],
            family=row["family"],
            artist=row["artist"],
            attribution=row["attribution"],
            work=row["work"],
            year=row["year"],
            licence=row["licence"],
            source_url=row["source_url"],
            commons_file=row["commons_file"],
            image_format=row["image_format"],
            width=int(row["width"]),
            height=int(row["height"]),
            image=row["image"],
        )

    def get(self, species: Species | str) -> Plate | None:
        """Find the plate for a species, by code then by either name."""
        if isinstance(species, str):
            return (
                self._row("species_code = ?", species)
                or self._row("scientific_name = ? COLLATE NOCASE", species)
                or self._row("common_name = ? COLLATE NOCASE", species)
            )
        plate = self._row("species_code = ?", species.code)
        if plate is None and species.scientific_name:
            plate = self._row(
                "scientific_name = ? COLLATE NOCASE", species.scientific_name
            )
        if plate is None and species.common_name:
            plate = self._row("common_name = ? COLLATE NOCASE", species.common_name)
        return plate

    def codes(self) -> set[str]:
        try:
            rows = self._connection.execute("SELECT species_code FROM plates")
        except sqlite3.Error:
            return set()
        return {row["species_code"] for row in rows}

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> PlateStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
