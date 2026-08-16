"""Print a palette test card, for tuning [palette] against the real panel.

Six swatches with a greyscale ramp and a colour ramp underneath. Send it
to the panel, photograph it in the light the display will actually live
in, sample the swatches, and put those RGB values in config.toml. Only
the preview PNG changes -- the panel is sent ink indices either way.

    python -m scripts.palette_card --out card.png
    python -m scripts.show_image card.png       # on the Pi

On an Inky Frame there is no "on the Pi" -- the card has to travel the same
road a board does, so write it in pen order and serve it:

    python -m scripts.palette_card --backend inky --out board.png
    python -m scripts.serve_board                # point secrets.py here
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.config import load_config  # noqa: E402
from birddisplay.display.inky_export import InkyExportDisplay  # noqa: E402
from birddisplay.render import fonts as F  # noqa: E402
from birddisplay.render.palette import Ink, flat_palette, to_rgb  # noqa: E402

NAMES = ["BLACK", "WHITE", "YELLOW", "RED", "BLUE", "GREEN"]


def build_card(config) -> Image.Image:
    # board_size, not width/height: the card has to travel the same road a
    # board does, and since the frame hangs portrait that road starts at
    # 480x800 and is turned on the way out. Building it at panel size got
    # the card refused by the exporter -- "board is (800, 480), frame
    # expects (480, 800)" -- which is the check doing its job.
    width, height = config.display.board_size
    card = Image.new("P", (width, height), int(Ink.WHITE))
    card.putpalette(flat_palette(config.palette))
    draw = ImageDraw.Draw(card)

    label_font = F.load_font("sans_bold", 15)
    small_font = F.load_font("sans", 12)

    # Row 1: flat swatches, each labelled in a colour that shows on it.
    swatch_w = width // 6
    swatch_h = 190
    for index, name in enumerate(NAMES):
        x = index * swatch_w
        draw.rectangle((x, 0, x + swatch_w, swatch_h), fill=index)
        ink = Ink.BLACK if index in (Ink.WHITE, Ink.YELLOW) else Ink.WHITE
        draw.text((x + 10, swatch_h - 26), name, font=label_font, fill=int(ink))
        draw.text((x + 10, 10), str(index), font=small_font, fill=int(ink))

    # Row 2: every two-ink checkerboard, which is what dithering actually
    # produces. Reading these off the panel tells you more about mixed
    # tones than the flat swatches do.
    y = swatch_h + 14
    pair_h = 120
    pairs = [(a, b) for a in range(6) for b in range(a + 1, 6)]
    pair_w = width // len(pairs)
    for index, (a, b) in enumerate(pairs):
        x = index * pair_w
        for row in range(pair_h):
            for col in range(pair_w):
                draw.point((x + col, y + row), fill=a if (row + col) % 2 else b)

    # Row 3: black-and-white ramp, to judge how the panel handles neutrals.
    y += pair_h + 14
    ramp_h = height - y - 30
    for col in range(width):
        density = col / width
        for row in range(ramp_h):
            lit = ((row * 7 + col * 3) % 16) / 16.0
            draw.point(
                (col, y + row), fill=int(Ink.BLACK if lit < density else Ink.WHITE)
            )
    draw.text(
        (10, height - 24),
        "black/white dither ramp -- neutrals should read grey, not blue",
        font=small_font,
        fill=int(Ink.BLACK),
    )
    return card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--out", default="palette_card.png")
    parser.add_argument(
        "--backend",
        choices=("preview", "inky"),
        default="preview",
        help='"inky" writes the card the way a board is written -- '
        "palette indices in PicoGraphics pen order -- so it can be served "
        "to an Inky Frame. This is the check that says pen 2 really is "
        "green on your panel, which Pimoroni's docs do not.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    card = build_card(config)
    out = Path(args.out)

    if args.backend == "inky":
        display = InkyExportDisplay(config, path=out)
        display.headline = "palette card"
        display.show(card)
    else:
        to_rgb(card, config.palette).save(out)
        print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
