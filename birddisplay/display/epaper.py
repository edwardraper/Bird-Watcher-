"""Waveshare 7.3in Spectra 6 (epd7in3e) backend.

Two things matter here and both are about not destroying the panel:

* epd.sleep() is called from a finally block, always. Leaving a Spectra 6
  powered in its active state is the main way people damage these, so the
  sleep must survive an exception in the middle of a refresh.
* We pack the buffer ourselves instead of calling epd.getbuffer(), which
  would re-quantise our board with Floyd-Steinberg and shred the text.

This module imports the driver lazily: epdconfig instantiates a
RaspberryPi object (and imports spidev and gpiozero) at import time, so it
cannot even be imported on a laptop.
"""

from __future__ import annotations

import logging
from typing import Any

from PIL import Image

from ..config import Config
from ..render.palette import pack_for_panel

log = logging.getLogger(__name__)


class EPaperUnavailable(Exception):
    """The driver could not be loaded -- almost always "not on a Pi"."""


def _load_driver() -> Any:
    try:
        from ..vendor import epd7in3e
    except Exception as exc:  # noqa: BLE001 - spidev/gpiozero blow up variously
        raise EPaperUnavailable(
            "could not load the epd7in3e driver. This backend only works on "
            "the Pi with SPI enabled (sudo raspi-config nonint do_spi 0) and "
            "python3-gpiozero installed. Use --preview elsewhere."
        ) from exc
    return epd7in3e


class EPaperDisplay:
    """A refresh is init -> display -> sleep, and sleep always happens."""

    def __init__(self, config: Config):
        self.config = config
        self.width = config.display.width
        self.height = config.display.height
        self._driver = _load_driver()
        self.epd = self._driver.EPD()
        # sleep() also tears down SPI and GPIO, so doing it twice in a row
        # throws. Track it rather than swallowing a confusing traceback.
        self._asleep = True
        if (self.epd.width, self.epd.height) != (self.width, self.height):
            log.warning(
                "config says %dx%d but the driver reports %dx%d",
                self.width,
                self.height,
                self.epd.width,
                self.epd.height,
            )

    def show(self, image: Image.Image) -> None:
        if image.mode != "P":
            raise ValueError(
                "EPaperDisplay expects a palettised board -- see render.palette"
            )
        image = self._to_panel(image)
        if image.size != (self.width, self.height):
            raise ValueError(
                f"board is {image.size}, panel is {(self.width, self.height)}"
            )

        buffer = pack_for_panel(image)
        log.info("refreshing panel (this takes ~30s)")
        try:
            self.epd.init()
            self._asleep = False
            self.epd.display(buffer)
        finally:
            # Non-negotiable: the panel must not be left awake.
            self._sleep()
        log.info("panel refresh complete")

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

    def clear(self) -> None:
        """Blank the panel to white. Use before storing it for a while."""
        try:
            self.epd.init()
            self._asleep = False
            self.epd.Clear()
        finally:
            self._sleep()

    def _sleep(self) -> None:
        if self._asleep:
            return
        try:
            self.epd.sleep()
        except Exception:  # noqa: BLE001 - a failed sleep must not mask the real error
            log.exception("failed to put the panel to sleep")
        finally:
            self._asleep = True

    def close(self) -> None:
        self._sleep()

    def __enter__(self) -> EPaperDisplay:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
