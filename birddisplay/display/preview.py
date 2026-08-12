"""PNG-writing display, so all layout work happens on a laptop.

The panel takes ~30s per refresh and there is no partial update, so
iterating on layout against real hardware would be miserable. This backend
is where milestones M3 and M4 actually get done.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from ..config import Config
from ..render.palette import to_rgb

log = logging.getLogger(__name__)


class PreviewDisplay:
    def __init__(self, config: Config, path: Path | None = None, scale: int = 1):
        self.config = config
        self.width = config.display.width
        self.height = config.display.height
        self.path = Path(path or config.display.preview_path).expanduser()
        self.scale = max(1, scale)

    def show(self, image: Image.Image) -> None:
        rgb = to_rgb(image, self.config.palette) if image.mode == "P" else image.convert("RGB")
        if self.scale > 1:
            # NEAREST so the dither pattern stays visible when zoomed.
            rgb = rgb.resize(
                (rgb.width * self.scale, rgb.height * self.scale),
                Image.Resampling.NEAREST,
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(self.path)
        log.info("wrote preview to %s", self.path.resolve())

    def close(self) -> None:
        pass

    def __enter__(self) -> PreviewDisplay:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
