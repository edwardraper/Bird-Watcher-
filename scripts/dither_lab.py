"""Render one photo at several enhancement settings, side by side.

Screen preview and panel output do not match, and the panel is the one
that counts. So: run this, send the sheet to the panel with

    python -m scripts.show_image /tmp/dither_lab.png

and pick the tile that looks best on the wall. Then copy those numbers
into [image] in config.toml.

    python -m scripts.dither_lab photo.jpg [--out /tmp/dither_lab.png]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.config import load_config  # noqa: E402
from birddisplay.render import fonts as F  # noqa: E402
from birddisplay.render.palette import (  # noqa: E402
    Ink,
    fit_cover,
    flat_palette,
    quantize_photo,
)

# (saturation, contrast, sharpness). The first is "no help at all", which
# is worth seeing: it shows how much work the enhancement is doing.
SETTINGS = [
    (1.0, 1.0, 1.0),
    (1.15, 1.10, 1.2),
    (1.30, 1.18, 1.45),
    (1.45, 1.25, 1.6),
    (1.60, 1.35, 1.8),
    (1.30, 1.18, 1.45),
]

TILE_W, TILE_H = 260, 195
COLS, ROWS = 3, 2
LABEL_H = 22
MARGIN = 8


def build_sheet(source: Image.Image, config, dither_last: bool = False) -> Image.Image:
    width = COLS * TILE_W + (COLS + 1) * MARGIN
    height = ROWS * (TILE_H + LABEL_H) + (ROWS + 1) * MARGIN

    sheet = Image.new("P", (width, height), int(Ink.WHITE))
    sheet.putpalette(flat_palette(config.palette))
    draw = ImageDraw.Draw(sheet)
    font = F.load_font("sans_bold", 13)

    cropped = fit_cover(source, TILE_W, TILE_H)
    for index, (saturation, contrast, sharpness) in enumerate(SETTINGS):
        col, row = index % COLS, index // COLS
        x = MARGIN + col * (TILE_W + MARGIN)
        y = MARGIN + row * (TILE_H + LABEL_H + MARGIN)

        settings = replace(
            config.image,
            saturation=saturation,
            contrast=contrast,
            sharpness=sharpness,
        )
        # The final tile repeats the middle setting without dithering, so
        # you can see exactly what the error diffusion is buying you.
        undithered = dither_last and index == len(SETTINGS) - 1
        tile = quantize_photo(
            cropped, settings, config.palette, dither=not undithered
        )
        sheet.paste(tile, (x, y))

        label = (
            "no dither"
            if undithered
            else f"sat {saturation:.2f}  con {contrast:.2f}  sharp {sharpness:.2f}"
        )
        draw.text((x, y + TILE_H + 4), label, font=font, fill=int(Ink.BLACK))

    return sheet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", help="path to a JPEG or PNG")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--out", default="dither_lab.png")
    parser.add_argument(
        "--rgb",
        action="store_true",
        help="also write an unquantised reference beside the sheet",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    with Image.open(args.photo) as handle:
        source = handle.convert("RGB")

    sheet = build_sheet(source, config, dither_last=True)

    from birddisplay.render.palette import to_rgb

    out = Path(args.out)
    to_rgb(sheet, config.palette).save(out)
    print(f"wrote {out.resolve()}")

    if args.rgb:
        reference = out.with_name(out.stem + "-reference" + out.suffix)
        fit_cover(source, TILE_W * 2, TILE_H * 2).save(reference)
        print(f"wrote {reference.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
