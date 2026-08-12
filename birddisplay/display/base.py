"""The display interface, and the factory that picks a backend.

One interface, three implementations. Everything upstream of here composes
an 800x480 palettised image and hands it over; only the backend knows
whether that ends up on a panel wired to this machine, in a preview PNG,
or in a file an Inky Frame will fetch over WiFi two hours from now.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from PIL import Image

from ..config import Config

log = logging.getLogger(__name__)


@runtime_checkable
class Display(Protocol):
    width: int
    height: int

    def show(self, image: Image.Image) -> None:
        """Put a composed board in front of a human."""

    def close(self) -> None:
        """Release the hardware. Must be safe to call twice."""


def create_display(
    config: Config,
    prefer_preview: bool = False,
    preview_path: Path | None = None,
    scale: int = 1,
    backend: str | None = None,
) -> Display:
    """Build the configured display backend.

    prefer_preview (the --preview flag) always wins, so you can render on
    the Pi without waking the panel. An explicit backend argument beats
    the config file but not --preview. preview_path and scale are ignored
    by the panel and export backends.
    """
    chosen = "preview" if prefer_preview else (backend or config.display.backend)
    if chosen == "preview":
        from .preview import PreviewDisplay

        return PreviewDisplay(config, path=preview_path, scale=scale)

    if chosen == "inky":
        from .inky_export import InkyExportDisplay

        return InkyExportDisplay(config, path=preview_path)

    from .epaper import EPaperDisplay

    return EPaperDisplay(config)
