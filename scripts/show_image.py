"""Push an arbitrary image to the panel. For tuning, not for the wall.

Photographs are dithered with the configured [image] settings; anything
already reduced to the six inks (a palette card, a dither-lab sheet) is
sent through untouched.

    python -m scripts.show_image dither_lab.png
    python -m scripts.show_image photo.jpg --preview
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.config import load_config  # noqa: E402
from birddisplay.display.base import create_display  # noqa: E402
from birddisplay.log import setup_logging  # noqa: E402
from birddisplay.render.palette import (  # noqa: E402
    Ink,
    fit_cover,
    new_canvas,
    quantize_photo,
)


def prepare(path: Path, config, exact: bool) -> Image.Image:
    width, height = config.display.width, config.display.height
    with Image.open(path) as handle:
        source = handle.convert("RGB")

    if exact:
        # Already six-colour: quantise without dithering so a test card
        # comes back off the panel exactly as it went on.
        fitted = source if source.size == (width, height) else fit_cover(
            source, width, height
        )
        return quantize_photo(fitted, config.image, config.palette, dither=False)

    canvas = new_canvas(width, height, config.palette, background=Ink.WHITE)
    canvas.paste(
        quantize_photo(fit_cover(source, width, height), config.image, config.palette),
        (0, 0),
    )
    return canvas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--preview", action="store_true", help="write a PNG instead")
    parser.add_argument(
        "--exact",
        action="store_true",
        help="the image is already six-colour; do not dither it",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    config = load_config(args.config)
    image = prepare(Path(args.image), config, exact=args.exact)

    display = create_display(config, prefer_preview=args.preview)
    try:
        display.show(image)
    finally:
        display.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
