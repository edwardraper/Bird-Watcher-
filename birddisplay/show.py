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
from pathlib import Path
from typing import Sequence

from .cache import CacheCorrupt, CacheMissing, load_board
from .config import Config, ConfigError, load_config
from .display.base import create_display
from .log import setup_logging
from .render.layout import BoardRenderer

log = logging.getLogger("birddisplay.show")


def run(
    config: Config,
    preview: bool = False,
    preview_path: Path | None = None,
    scale: int = 1,
) -> int:
    renderer = BoardRenderer(config)

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
        image = renderer.render(board)

    display = create_display(
        config, prefer_preview=preview, preview_path=preview_path, scale=scale
    )
    try:
        display.show(image)
    finally:
        display.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the cached board.")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="write a PNG instead of driving the panel",
    )
    parser.add_argument("--preview-path", help="where to write the preview PNG")
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
            preview=args.preview or bool(args.preview_path),
            preview_path=Path(args.preview_path) if args.preview_path else None,
            scale=args.scale,
        )
    except Exception:  # noqa: BLE001 - a render crash must not leave the panel awake
        log.exception("failed to draw the board")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
