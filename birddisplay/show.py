"""Entrypoint: render the cache onto a display. Never touches the network.

Runs ten minutes after the fetcher. It reads whatever cache is on disk and
draws it -- including when that cache is old, in which case the board
carries a "data stale" marker rather than quietly presenting last week's
sightings as this morning's.

    python -m birddisplay.show --preview
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from PIL import Image, UnidentifiedImageError

from .cache import CacheCorrupt, CacheMissing, load_board
from .config import Config, ConfigError, load_config
from .display.base import create_display
from .log import setup_logging
from .model import Board
from .render.layout import BoardRenderer
from .render.plate_board import PlateBoardRenderer

log = logging.getLogger("birddisplay.show")


def load_headline_image(board: Board) -> Image.Image | None:
    """Whatever illustrates the headline: a cut-out plate or a photograph.

    Which layout to draw is decided by what the image *is* rather than by
    anything recorded in the cache: a plate has had its paper removed and
    so carries an alpha channel, and a photograph is a solid rectangle.
    That keeps the cache schema out of it, and means a cache written
    before plates existed still renders correctly.
    """
    photo = board.headline_photo
    if photo is None or not photo.path:
        return None
    path = Path(photo.path)
    if not path.exists():
        log.warning("headline image missing from disk: %s", path)
        return None
    try:
        with Image.open(path) as handle:
            handle.load()
            return handle.convert("RGBA" if "A" in handle.getbands() else "RGB")
    except (OSError, UnidentifiedImageError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return None


def run(
    config: Config,
    preview: bool = False,
    preview_path: Path | None = None,
    scale: int = 1,
    backend: str | None = None,
) -> int:
    renderer = BoardRenderer(config)
    headline = ""
    board: Board | None = None

    try:
        board = load_board(config.cache.board_path)
    except CacheMissing:
        log.warning("no cache yet; drawing the first-run placeholder")
        image = renderer.render_placeholder(
            "Waiting for the first fetch",
            "Run python -m birddisplay.fetch, or check "
            "journalctl -u birddisplay-fetch.",
        )
    except CacheCorrupt as exc:
        log.error("cache unreadable: %s", exc)
        image = renderer.render_placeholder("Cache unreadable", str(exc))
    else:
        age = board.age_hours()
        if board.is_stale(config.cache.max_age_hours):
            log.warning("cache is %.1fh old; marking the board stale", age)
        else:
            log.info("rendering board generated %.1fh ago", age)
        if board.headline is not None:
            headline = board.headline.species.common_name
        headline_image = load_headline_image(board)
        is_plate = headline_image is not None and "A" in headline_image.getbands()
        # The quiet board takes either kind of picture. The photo board is
        # built around a 480px square on the left of a landscape panel, so
        # it has nothing to say about a frame hanging on its side.
        if config.display.portrait or is_plate:
            log.info(
                "drawing the quiet board (%s)",
                "plate" if is_plate else "photograph",
            )
            image = PlateBoardRenderer(config).render(board, headline_image)
        else:
            image = renderer.render(board)

    display = create_display(
        config,
        prefer_preview=preview,
        preview_path=preview_path,
        scale=scale,
        backend=backend,
    )
    # The export backend puts the headline in its manifest, so a frame can
    # say what it is showing without decoding the PNG.
    if hasattr(display, "headline"):
        display.headline = headline
    try:
        display.show(image)
        # One extra board per other sighting, so a button on the frame has
        # somewhere to go. Only the export backend has any use for these:
        # a panel shows one thing at a time, and a preview PNG is a file.
        if board is not None and hasattr(display, "show_alternate"):
            render_alternates(config, board, display)
    finally:
        display.close()
    return 0


def render_alternates(config: Config, board: Board, display) -> None:
    """Draw a whole board for each of the other sightings.

    An alternate is the same board with a different bird at the top of
    it, so it goes through the same renderer with the headline swapped --
    which is why Feature carries a sighting, a picture and a sentence:
    those are exactly the three things the headline slot needs.

    A failure here costs a button press, not the board. The main PNG is
    already written by the time this runs, and the frame falls back to it
    when an alternate it asks for is not in the manifest.
    """
    for index, feature in enumerate(board.alternates, start=1):
        swapped = replace(
            board,
            headline=feature.sighting,
            headline_photo=feature.photo,
            headline_description=feature.description,
            # The bird now at the top should not also be in the list
            # underneath itself.
            also_seen=[
                s
                for s in board.also_seen
                if s.species.code != feature.sighting.species.code
            ],
        )
        try:
            image = load_headline_image(swapped)
            is_plate = image is not None and "A" in image.getbands()
            if config.display.portrait or is_plate:
                canvas = PlateBoardRenderer(config).render(swapped, image)
            else:
                canvas = BoardRenderer(config).render(swapped)
            display.show_alternate(canvas, feature.sighting.species.common_name)
        except Exception:  # noqa: BLE001 - one missing alternate is survivable
            log.exception(
                "could not draw the alternate board for %s",
                feature.sighting.species.common_name,
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the cached board.")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="write a PNG instead of driving the panel",
    )
    parser.add_argument("--preview-path", help="where to write the output PNG")
    parser.add_argument(
        "--backend",
        choices=("epaper", "preview", "inky"),
        help="override [display].backend: the panel, a preview PNG, or a "
        "board.png plus manifest for an Inky Frame to fetch",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=1,
        help="upscale the preview by this factor (preview only)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        return run(
            config,
            preview=args.preview or (bool(args.preview_path) and not args.backend),
            preview_path=Path(args.preview_path) if args.preview_path else None,
            scale=args.scale,
            backend=args.backend,
        )
    except Exception:  # noqa: BLE001 - a render crash must not leave the panel awake
        log.exception("failed to draw the board")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
