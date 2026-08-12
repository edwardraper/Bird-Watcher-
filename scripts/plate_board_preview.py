"""Render the plate board to a PNG, from the cache or from sample data.

    python -m scripts.plate_board_preview --species eurbla --scale 2

With no cache to hand it makes up a plausible Devon board -- a headline
species plus a handful of other sightings -- so the layout can be judged
before the hardware exists. The sample sightings are invented; anything
real comes from ~/.cache/birddisplay/cache.json when it is there.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.cache import load_board  # noqa: E402
from birddisplay.config import ConfigError, load_config  # noqa: E402
from birddisplay.display.preview import PreviewDisplay  # noqa: E402
from birddisplay.log import setup_logging  # noqa: E402
from birddisplay.model import Board, Photo, Sighting, Species  # noqa: E402
from birddisplay.render.plate_board import PlateBoardRenderer  # noqa: E402
from birddisplay.sources.plates import PlateStore  # noqa: E402

log = logging.getLogger("plate_board_preview")

DEFAULT_DB = Path(__file__).resolve().parent.parent / "assets" / "plates" / "keulemans_uk.sqlite"

# Sample sightings: real places on the Exe estuary, invented observations.
SAMPLE_LOCATION = "Bowling Green Marsh RSPB"
SAMPLE_ALSO_SEEN: tuple[tuple[str, str, str], ...] = (
    ("whimbr", "Whimbrel", "Numenius phaeopus"),
    ("comgre1", "Greenshank", "Tringa nebularia"),
    ("litegr", "Little Egret", "Egretta garzetta"),
    ("bktgod", "Black-tailed Godwit", "Limosa limosa"),
    ("comsan", "Common Sandpiper", "Actitis hypoleucos"),
    ("cetwar1", "Cetti's Warbler", "Cettia cetti"),
    ("eurkes", "Kestrel", "Falco tinnunculus"),
    ("comred1", "Redshank", "Tringa totanus"),
)


def sample_board(store: PlateStore, code: str, region_name: str) -> Board:
    plate = store.get(code)
    if plate is None:
        raise SystemExit(
            f"no plate for {code!r} in {store.path}. "
            f"Try one of: {', '.join(sorted(store.codes()))}"
        )

    now = datetime.now()
    headline = Sighting(
        species=Species(
            code=plate.species_code,
            common_name=plate.common_name,
            scientific_name=plate.scientific_name,
            family=plate.family,
        ),
        location=SAMPLE_LOCATION,
        observed_at=now.replace(hour=7, minute=14, second=0, microsecond=0),
        count=3,
        notable=False,
    )
    also_seen = [
        Sighting(
            species=Species(code=code, common_name=name, scientific_name=sci),
            location=SAMPLE_LOCATION,
            observed_at=now - timedelta(hours=index + 1),
            notable=True,
        )
        for index, (code, name, sci) in enumerate(SAMPLE_ALSO_SEEN)
    ]
    return Board(
        generated_at=datetime.now(timezone.utc),
        region_name=region_name,
        headline=headline,
        headline_photo=Photo(
            path="",
            # plate.credit, not plate.artist: the artist column is a
            # constant the builder stamps on every row, and the preview
            # must show the same credit the panel will print.
            credit=plate.credit,
            licence=plate.licence,
            source_url=plate.source_url,
        ),
        also_seen=also_seen,
        species_count=len(also_seen) + 1,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--plates", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--species",
        default="eurbla",
        help="eBird code, scientific name or common name of the headline bird",
    )
    parser.add_argument("--out", type=Path, default=Path("preview.png"))
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument(
        "--cache",
        action="store_true",
        help="use the real cached board instead of sample sightings",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    store = PlateStore.maybe(args.plates)
    if store is None:
        log.error("no plate database at %s; run scripts.build_plate_db", args.plates)
        return 1

    if args.cache:
        board = load_board(config.cache.board_path)
        if board is None or board.headline is None:
            log.error("no usable cached board; drop --cache for sample data")
            return 1
        plate = store.get(board.headline.species)
    else:
        board = sample_board(store, args.species, config.region.name)
        plate = store.get(args.species)

    if plate is None:
        log.error("no plate for the headline species")
        return 1

    with Image.open(io.BytesIO(plate.image)) as handle:
        handle.load()
        image = handle.convert("RGBA")

    canvas = PlateBoardRenderer(config).render(board, image)
    PreviewDisplay(config, path=args.out, scale=args.scale).show(canvas)
    log.info(
        "%s (%s), plate from %s", plate.common_name, plate.species_code, plate.work
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
