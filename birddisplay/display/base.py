"""The display interface, and the factory that picks a backend.

One interface, two implementations. Everything upstream of here composes
an 800x480 palettised image and hands it over; only the backend knows
whether that ends up on a panel or in a PNG.
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
) -> Display:
    """Build the configured display backend.

    prefer_preview (the --preview flag) always wins, so you can render on
    the Pi without waking the panel. preview_path and scale are ignored by
    the e-paper backend.
    """
    backend = "preview" if prefer_preview else config.display.backend
    if backend == "preview":
        from .preview import PreviewDisplay

        return PreviewDisplay(config, path=preview_path, scale=scale)

    from .epaper import EPaperDisplay

    return EPaperDisplay(config)
