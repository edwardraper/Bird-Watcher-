"""Draw the six pens by number, to find out what each one actually prints.

    python -m scripts.pen_probe --out board.png
    python -m scripts.serve_board

The palette card asks "is pen 2 green?" and answers through
INKY_PEN_CODES -- which is fine when that table is right and useless when
it is wrong, because then every swatch is mislabelled and the reader has
to invert a mapping in their head from a photograph.

This writes the pen indices straight into the file. No ink names, no
translation table, nothing to reason through: band 0 is pen 0, band 5 is
pen 5, each labelled with its own number. Photograph the panel, read off
what colour each number came out, and INKY_PEN_CODES follows directly.

Pimoroni's PicoGraphics documentation describes the older seven-colour
Inky Frame, so on a Spectra 6 panel this is the only reliable source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.config import load_config  # noqa: E402
from birddisplay.render import fonts as F  # noqa: E402

# Nominal look of each pen, for anyone opening the PNG on a laptop. The
# frame ignores these entirely -- PNG_COPY copies indices, not colours --
# so they are deliberately just "six distinguishable things" rather than
# any claim about what the panel will print.
PROBE_RGB = (
    (0, 0, 0),
    (255, 255, 255),
    (0, 200, 0),
    (0, 0, 255),
    (255, 0, 0),
    (255, 255, 0),
    (243, 132, 30),
    (200, 190, 175),
)

PENS = 6


def build_probe(width: int, height: int) -> Image.Image:
    """Six numbered bands, each filled with its own pen index."""
    card = Image.new("P", (width, height), 1)
    flat: list[int] = []
    for rgb in PROBE_RGB:
        flat.extend(rgb)
    flat.extend([0] * (768 - len(flat)))
    card.putpalette(flat)

    draw = ImageDraw.Draw(card)
    band = height // PENS
    number_font = F.load_font("sans_bold", 96)

    for pen in range(PENS):
        top = pen * band
        draw.rectangle((0, top, width, top + band), fill=pen)
        # The number twice, in the two pens that are certainly readable
        # against anything: whatever this band turns out to be, one of
        # them will show. Pen 0 and pen 1 are black and white on every
        # panel anyone has ever shipped.
        draw.text((30, top + 12), str(pen), font=number_font, fill=1)
        draw.text((width - 110, top + 12), str(pen), font=number_font, fill=0)

    return card


def write_manifest(path: Path, payload: bytes, width: int, height: int) -> None:
    manifest = {
        "schema_version": 1,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "width": width,
        "height": height,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "image": path.name,
        "headline": "pen probe",
    }
    path.with_suffix(".json").write_text(json.dumps(manifest, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--out", type=Path, default=Path("board.png"))
    args = parser.parse_args(argv)

    config = load_config(args.config)
    width, height = config.display.board_size
    card = build_probe(width, height)

    # The same turn the display backend would apply, so the numbers read
    # the right way up in the orientation the frame is held.
    if config.display.rotate == 90:
        card = card.transpose(Image.Transpose.ROTATE_90)
    elif config.display.rotate == 180:
        card = card.transpose(Image.Transpose.ROTATE_180)
    elif config.display.rotate == 270:
        card = card.transpose(Image.Transpose.ROTATE_270)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # No optimize: the indices are the payload, exactly as in inky_export.
    card.save(args.out, format="PNG", compress_level=9)
    payload = args.out.read_bytes()
    write_manifest(args.out, payload, card.width, card.height)

    print("wrote %s (%d bytes) and its manifest" % (args.out, len(payload)))
    print("Photograph the panel and report the colour of each numbered band.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
