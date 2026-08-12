"""Lay the built plates out on a sheet, so a human can look at them.

    python -m scripts.plate_contact_sheet --from-rank 101 --to-rank 150 \
        --out /tmp/sheet.png

The builder's score says the heuristics were satisfied: right artist, right
bird, background came away. It does not say the file is a picture of a
bird. A page of eight moorhen bills is genuinely Keulemans, genuinely a
moorhen, and cuts to bare paper beautifully -- it scores well and it is
useless on the board. The only way to catch that is to look at every plate,
and opening two hundred WebP blobs one at a time is not looking, it is
scrolling.

So: the cut-outs in a grid, each labelled with its rank, its name and where
it came from, at a size where a wrong bird is obvious. Each plate sits on a
pale panel rather than on white, because the cut-out is transparent where
the paper was and a bird missing a leg looks fine against white and wrong
against grey. Sheets are split at --per-sheet so the cells stay big; the
whole point is lost the moment a plate is too small to read.

Nothing here runs on the Pi.
"""

from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.log import setup_logging  # noqa: E402
from birddisplay.render import fonts as F  # noqa: E402

log = logging.getLogger("plate_contact_sheet")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "assets" / "plates" / "keulemans_uk.sqlite"

SHEET_BG = (255, 255, 255)
CELL_BG = (233, 231, 227)
RULE = (196, 192, 186)
INK = (24, 24, 24)
FAINT = (110, 108, 104)

MARGIN = 28
GUTTER = 18
LABEL_HEIGHT = 62
PAD = 10


@dataclass(frozen=True)
class Cell:
    """One plate, ready to draw: the image and the three lines under it."""

    rank: int
    common_name: str
    scientific_name: str
    source: str
    image: Image.Image


def read_plates(path: Path, first: int, last: int) -> list[Cell]:
    """Plates in the rank range, in rank order.

    Read straight from SQLite rather than through PlateStore: the store
    answers "is there a plate for this bird?", which is the Pi's question,
    and this tool wants every row in a range with its rank attached.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT rank, common_name, scientific_name, work, commons_file, image"
            " FROM plates WHERE rank BETWEEN ? AND ? ORDER BY rank",
            (first, last),
        ).fetchall()
    finally:
        connection.close()

    cells = []
    for row in rows:
        with Image.open(io.BytesIO(row["image"])) as handle:
            handle.load()
            image = handle.convert("RGBA")
        # `work` is the recognised book where there was one and the bare
        # filename where there was not, and the filename is the more useful
        # of the two when a plate turns out to be wrong.
        source = row["work"] or row["commons_file"]
        if source != row["commons_file"]:
            source = f"{source.split(',')[0]} -- {row['commons_file']}"
        cells.append(
            Cell(
                rank=int(row["rank"]),
                common_name=row["common_name"],
                scientific_name=row["scientific_name"],
                source=source,
                image=image,
            )
        )
    return cells


def _fit(image: Image.Image, box: tuple[int, int]) -> Image.Image:
    width, height = box
    scale = min(width / image.width, height / image.height, 1.0)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


def _elide(draw: ImageDraw.ImageDraw, text: str, font, limit: int) -> str:
    if draw.textlength(text, font=font) <= limit:
        return text
    while text and draw.textlength(text + "…", font=font) > limit:
        text = text[:-1]
    return text + "…"


def draw_sheet(cells: Sequence[Cell], columns: int, cell_width: int, title: str) -> Image.Image:
    """One sheet: a grid of plates with their labels underneath."""
    cell_height = round(cell_width * 1.15)
    rows = (len(cells) + columns - 1) // columns
    width = MARGIN * 2 + columns * cell_width + (columns - 1) * GUTTER
    header = 44
    height = (
        MARGIN * 2
        + header
        + rows * (cell_height + LABEL_HEIGHT)
        + (rows - 1) * GUTTER
    )

    sheet = Image.new("RGB", (width, height), SHEET_BG)
    draw = ImageDraw.Draw(sheet)

    heading_font = F.load_font("sans_bold", 20)
    name_font = F.load_font("sans_bold", 16)
    sci_font = F.load_font("serif_italic", 14)
    note_font = F.load_font("sans", 12)

    draw.text((MARGIN, MARGIN), title, font=heading_font, fill=INK)
    draw.line(
        (MARGIN, MARGIN + header - 12, width - MARGIN, MARGIN + header - 12), fill=RULE
    )

    for index, cell in enumerate(cells):
        column, row = index % columns, index // columns
        x = MARGIN + column * (cell_width + GUTTER)
        y = MARGIN + header + row * (cell_height + LABEL_HEIGHT + GUTTER)

        draw.rectangle((x, y, x + cell_width, y + cell_height), fill=CELL_BG)
        draw.rectangle((x, y, x + cell_width, y + cell_height), outline=RULE)

        plate = _fit(cell.image, (cell_width - PAD * 2, cell_height - PAD * 2))
        sheet.paste(
            plate,
            (
                x + (cell_width - plate.width) // 2,
                y + (cell_height - plate.height) // 2,
            ),
            plate,
        )

        text_y = y + cell_height + 6
        limit = cell_width - 4
        draw.text(
            (x, text_y),
            _elide(draw, f"{cell.rank}. {cell.common_name}", name_font, limit),
            font=name_font,
            fill=INK,
        )
        draw.text(
            (x, text_y + 20),
            _elide(draw, cell.scientific_name, sci_font, limit),
            font=sci_font,
            fill=INK,
        )
        draw.text(
            (x, text_y + 38),
            _elide(draw, cell.source, note_font, limit),
            font=note_font,
            fill=FAINT,
        )

    return sheet


def sheet_paths(out: Path, count: int) -> list[Path]:
    """One name for one sheet, numbered names for several."""
    if count <= 1:
        return [out]
    return [out.with_name(f"{out.stem}-{n + 1}{out.suffix}") for n in range(count)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plates", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=Path("contact_sheet.png"))
    parser.add_argument("--from-rank", type=int, default=1)
    parser.add_argument("--to-rank", type=int, default=10_000)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument(
        "--cell-width",
        type=int,
        default=320,
        help="pixels per plate. Smaller fits more on a sheet and hides "
        "exactly the mistakes this tool exists to find",
    )
    parser.add_argument(
        "--per-sheet",
        type=int,
        default=12,
        help="plates per sheet; the rest spill onto -2, -3 and so on",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if not args.plates.exists():
        log.error("no plate database at %s; run scripts.build_plate_db", args.plates)
        return 1

    cells = read_plates(args.plates, args.from_rank, args.to_rank)
    if not cells:
        log.error("no plates between ranks %d and %d", args.from_rank, args.to_rank)
        return 1

    batches = [
        cells[start : start + args.per_sheet]
        for start in range(0, len(cells), args.per_sheet)
    ]
    paths = sheet_paths(args.out, len(batches))

    for number, (batch, path) in enumerate(zip(batches, paths), start=1):
        title = (
            f"Keulemans plates, ranks {batch[0].rank}-{batch[-1].rank}"
            f"  ({number}/{len(batches)}, {len(batch)} plates)"
        )
        sheet = draw_sheet(batch, args.columns, args.cell_width, title)
        path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(path)
        log.info("wrote %s (%dx%d)", path, sheet.width, sheet.height)

    print("\n".join(str(path.resolve()) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
