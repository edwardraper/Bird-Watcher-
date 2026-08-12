"""The Pi's side of the plate database: it must never take the board down."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from birddisplay.model import Species
from birddisplay.sources.plates import Plate, PlateStore

PLATE_ROW = {
    "species_code": "eurrob1",
    "rank": 3,
    "common_name": "Robin",
    "scientific_name": "Erithacus rubecula",
    "family": "Old World Flycatchers",
    "artist": "J. G. Keulemans",
    "attribution": "signed on the plate",
    "work": "Lilford, Coloured Figures of the Birds of the British Islands",
    "year": "between 1885 and 1897",
    "licence": "Public domain",
    "source_url": "https://commons.wikimedia.org/wiki/File:Robin.jpg",
    "commons_file": "Robin.jpg",
    "image_format": "webp",
    "width": 640,
    "height": 900,
    "sha256": "0" * 64,
    "image": b"not-really-an-image",
}


def make_db(path: Path, version: str = "1", rows=(PLATE_ROW,)) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE plates (
            species_code TEXT PRIMARY KEY, rank INTEGER, common_name TEXT,
            scientific_name TEXT, family TEXT, artist TEXT, attribution TEXT,
            work TEXT, year TEXT, licence TEXT, source_url TEXT,
            commons_file TEXT, image_format TEXT, width INTEGER,
            height INTEGER, sha256 TEXT, image BLOB
        );
        """
    )
    for row in rows:
        connection.execute(
            f"INSERT INTO plates ({', '.join(row)}) "
            f"VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
    connection.execute("INSERT INTO meta VALUES ('schema_version', ?)", (version,))
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def store(tmp_path: Path) -> PlateStore:
    store = PlateStore(make_db(tmp_path / "plates.sqlite"))
    yield store
    store.close()


def test_finds_a_plate_by_species_code(store: PlateStore) -> None:
    plate = store.get("eurrob1")
    assert plate is not None
    assert plate.common_name == "Robin"
    assert plate.image == b"not-really-an-image"


def test_finds_a_plate_by_either_name(store: PlateStore) -> None:
    """The database may have been built against an older taxonomy."""
    assert store.get("Erithacus rubecula") is not None
    assert store.get("erithacus rubecula") is not None
    assert store.get("Robin") is not None


def test_species_lookup_falls_back_from_a_renamed_code(store: PlateStore) -> None:
    renamed = Species(
        code="eurrob9", common_name="Robin", scientific_name="Erithacus rubecula"
    )
    plate = store.get(renamed)
    assert plate is not None and plate.species_code == "eurrob1"


def test_unknown_species_is_none_not_an_error(store: PlateStore) -> None:
    assert store.get("nosuchbird") is None
    assert store.get(Species(code="x", common_name="X", scientific_name="X x")) is None


def test_missing_database_returns_none(tmp_path: Path) -> None:
    assert PlateStore.maybe(tmp_path / "absent.sqlite") is None
    assert PlateStore.maybe(None) is None


def test_corrupt_database_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "junk.sqlite"
    path.write_bytes(b"this is not a database")
    store = PlateStore.maybe(path)
    # Either refused at open time or refused on its first query; what
    # matters is that nothing propagates to the renderer.
    assert store is None or store.get("eurrob1") is None


def test_a_future_schema_is_declined(tmp_path: Path) -> None:
    """Better no plates than plates read with the wrong shape."""
    assert PlateStore.maybe(make_db(tmp_path / "v9.sqlite", version="9")) is None


def test_count_and_codes(store: PlateStore) -> None:
    assert store.count() == 1
    assert store.codes() == {"eurrob1"}


def test_writing_the_image_out_is_atomic(store: PlateStore, tmp_path: Path) -> None:
    plate = store.get("eurrob1")
    assert plate is not None
    dest = plate.write_image(tmp_path / "images")
    assert dest.read_bytes() == b"not-really-an-image"
    assert dest.suffix == ".webp"
    assert not list(dest.parent.glob("*.part"))


def test_as_photo_carries_the_credit(store: PlateStore, tmp_path: Path) -> None:
    """The work is credited, not the `artist` column.

    This plate is the case that gives the game away: it comes out of
    Lilford, which had several illustrators, and the builder stamped
    "J. G. Keulemans" on it anyway because the Commons Artist field
    lists everyone who drew for the book. The panel printed that name.
    """
    plate = store.get("eurrob1")
    assert plate is not None
    assert plate.artist == "J. G. Keulemans", "the unreliable column, kept as-is"

    photo = plate.as_photo(tmp_path / "images")
    assert photo.credit == "Lilford, Coloured Figures"
    assert photo.credit_line == "Lilford, Coloured Figures / Public domain"
    assert Path(photo.path).exists()


def test_a_plate_from_keulemans_own_book_still_names_him() -> None:
    """Crediting the work does not lose the artist where he is known --
    his own book's title carries his name, which is exactly why the work
    is honest in both directions."""
    own_book = Plate(
        species_code="eurbla",
        common_name="Blackbird",
        scientific_name="Turdus merula",
        family="Thrushes",
        artist="J. G. Keulemans",
        attribution="attributed",
        work="Keulemans, Onze vogels in huis en tuin (1869)",
        year="1869",
        licence="Public domain",
        source_url="",
        commons_file="",
        image_format="webp",
        width=1,
        height=1,
        image=b"",
    )
    assert own_book.credit == "Keulemans, Onze vogels (1869)"
    assert own_book.credit_line == "Keulemans, Onze vogels (1869) / Public domain"


def test_credit_line_survives_a_missing_licence() -> None:
    bare = Plate(
        species_code="x",
        common_name="X",
        scientific_name="X x",
        family="",
        artist="J. G. Keulemans",
        attribution="attributed",
        work="",
        year="",
        licence="",
        source_url="",
        commons_file="",
        image_format="webp",
        width=1,
        height=1,
        image=b"",
    )
    # No work to name and no licence to state: credit Commons plainly
    # rather than repeating a name the row cannot support.
    assert bare.credit_line == "Wikimedia Commons"


def test_an_unidentified_work_is_not_printed_as_a_filename() -> None:
    """Where the builder could not match a work it stored the Commons
    file name, which is a filename and not a citation. The panel must not
    print "Anser albifrons 1921.jpg" at the foot of the board."""
    from dataclasses import replace as _replace

    bare = Plate(
        species_code="x",
        common_name="X",
        scientific_name="X x",
        family="",
        artist="J. G. Keulemans",
        attribution="attributed",
        work="Anser albifrons 1921.jpg",
        year="",
        licence="Public domain",
        source_url="",
        commons_file="",
        image_format="webp",
        width=1,
        height=1,
        image=b"",
    )
    assert bare.credit_line == "Wikimedia Commons / Public domain"
    assert ".jpg" not in _replace(bare, work="The Ibis").credit_line


def test_nothing_credits_the_artist_column() -> None:
    """The bug was in three places at once, so guard the shape of it.

    `Plate.artist` is the constant the builder stamps on every row. It is
    kept because it is what the database says, but nothing may print it:
    the credit comes from the work. This caught `as_photo` and the
    preview script after the first fix looked complete.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in (*(root / "birddisplay").rglob("*.py"), *(root / "scripts").glob("*.py")):
        if path.name == "build_plate_db.py":
            continue  # writes the column; does not print it
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bcredit\w*\s*=\s*\w+\.artist\b", line):
                offenders.append(f"{path.relative_to(root)}:{number}")
    assert not offenders, f"these credit the artist column: {offenders}"
