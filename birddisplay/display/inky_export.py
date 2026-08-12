"""Export backend: write the board where an Inky Frame can fetch it.

The Inky Frame 7.3 is a Pico 2 W, not a small Linux box. None of this
package runs on it: no CPython, no Pillow, no SQLite, and the plate
database is fifty times its free flash. So the split that used to be
"fetcher and renderer, joined by a cache file" becomes "GitHub Actions and
a picture frame, joined by an HTTPS URL", and this backend is the end of
the first half.

It writes two files:

    board.png   an 800x480 palettised PNG in Inky pen order
    board.json  sha256 of that PNG, when it was made, and what is on it

The sidecar is what makes a battery-powered frame worth the trouble. It is
a few hundred bytes, so a frame that wakes every two hours can ask "has
anything changed?" for almost nothing, and skip the download and the
thirty-second panel refresh when the answer is no. Sightings do not move
much between 2am and 4am.

Nothing here drives hardware, so it is also the safest backend to run in
CI, where there is no panel and no SPI.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from ..config import Config
from ..render.palette import to_inky_png

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class InkyExportDisplay:
    """Writes the board to disk for a frame to come and get."""

    def __init__(self, config: Config, path: Path | None = None):
        self.config = config
        self.width = config.display.width
        self.height = config.display.height
        self.path = Path(path or config.display.export_path).expanduser()
        self.headline = ""

    @property
    def manifest_path(self) -> Path:
        return self.path.with_suffix(".json")

    def show(self, image: Image.Image) -> None:
        if image.size != (self.width, self.height):
            raise ValueError(
                f"board is {image.size}, frame expects {(self.width, self.height)}"
            )
        if self.config.display.rotate == 180:
            image = image.rotate(180)

        exported = to_inky_png(image, self.config.palette)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Written to a temp file and moved into place: the workflow may be
        # publishing the previous one, and a half-written PNG is a frame
        # that fails to decode and leaves a stale board up for two hours.
        tmp = self.path.with_suffix(self.path.suffix + ".part")
        try:
            # No optimize=True. Pillow is entitled to drop unused palette
            # entries and renumber the rest, and the indices are the whole
            # payload -- see render.palette.to_inky_png.
            exported.save(tmp, format="PNG", compress_level=9)
            payload = tmp.read_bytes()
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)

        digest = hashlib.sha256(payload).hexdigest()
        self._write_manifest(digest, len(payload))
        log.info(
            "wrote %s (%.1f kB, sha %s)", self.path, len(payload) / 1024, digest[:12]
        )

    def _write_manifest(self, digest: str, size: int) -> None:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "sha256": digest,
            "bytes": size,
            "width": self.width,
            "height": self.height,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "image": self.path.name,
            "headline": self.headline,
        }
        tmp = self.manifest_path.with_suffix(".json.part")
        try:
            tmp.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
            tmp.replace(self.manifest_path)
        finally:
            tmp.unlink(missing_ok=True)

    def close(self) -> None:
        pass

    def __enter__(self) -> InkyExportDisplay:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
