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
        # One entry per extra board written, in the order the buttons
        # page through them. Rewritten into the manifest as each is
        # added, so a run cut short leaves a manifest describing only the
        # boards that actually exist on disk.
        self.alternates: list[dict[str, object]] = []
        self._digest, self._size = "", 0

    @property
    def manifest_path(self) -> Path:
        return self.path.with_suffix(".json")

    def _to_panel(self, image: Image.Image) -> Image.Image:
        """Turn a composed board to match how the frame hangs.

        expand=True so a 480x800 portrait board becomes the 800x480 the
        panel expects; Image.ROTATE_* is a transpose, so no resampling and
        no palette damage.
        """
        rotate = self.config.display.rotate
        if rotate == 90:
            return image.transpose(Image.Transpose.ROTATE_90)
        if rotate == 180:
            return image.transpose(Image.Transpose.ROTATE_180)
        if rotate == 270:
            return image.transpose(Image.Transpose.ROTATE_270)
        return image

    def show(self, image: Image.Image) -> None:
        if image.size != self.config.display.board_size:
            raise ValueError(
                f"board is {image.size}, frame expects "
                f"{self.config.display.board_size}"
            )
        payload = self._write_image(image, self.path)
        digest = hashlib.sha256(payload).hexdigest()
        self._write_manifest(digest, len(payload))
        log.info(
            "wrote %s (%.1f kB, sha %s)", self.path, len(payload) / 1024, digest[:12]
        )

    def _write_image(self, image: Image.Image, path: Path) -> bytes:
        """Turn a composed board and put it on disk, atomically.

        Written to a temp file and moved into place: the workflow may be
        publishing the previous one, and a half-written PNG is a frame
        that fails to decode and leaves a stale board up for two hours.
        """
        image = self._to_panel(image)
        exported = to_inky_png(image, self.config.palette)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp = path.with_suffix(path.suffix + ".part")
        try:
            # No optimize=True. Pillow is entitled to drop unused palette
            # entries and renumber the rest, and the indices are the whole
            # payload -- see render.palette.to_inky_png.
            exported.save(tmp, format="PNG", compress_level=9)
            payload = tmp.read_bytes()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return payload

    def alternate_path(self, index: int) -> Path:
        """board.png -> board-1.png, board-2.png, and so on."""
        return self.path.with_name(
            "%s-%d%s" % (self.path.stem, index, self.path.suffix)
        )

    def show_alternate(self, image: Image.Image, headline: str = "") -> None:
        """Write one more board, for a button to page to.

        Same file format and the same refusal to be re-quantised as the
        main board -- these are boards in every sense, just not the one
        the frame shows when it wakes on its own.
        """
        index = len(self.alternates) + 1
        path = self.alternate_path(index)
        payload = self._write_image(image, path)
        self.alternates.append(
            {
                "image": path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "headline": headline,
            }
        )
        # Rewritten now rather than at the end: the manifest must never
        # promise a board that is not on disk yet, because the frame
        # believes it.
        self._write_manifest(self._digest, self._size)
        log.info("wrote %s (%.1f kB, %s)", path, len(payload) / 1024, headline)

    def _write_manifest(self, digest: str, size: int) -> None:
        self._digest, self._size = digest, size
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "sha256": digest,
            "bytes": size,
            # Empty for a board with nothing else worth showing, which is
            # what a frame with an older manifest also sees.
            "alternates": self.alternates,
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
