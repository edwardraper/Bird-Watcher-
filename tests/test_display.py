from __future__ import annotations

import pytest
from PIL import Image

from birddisplay.display.base import Display, create_display
from birddisplay.display.preview import PreviewDisplay
from birddisplay.render.palette import Ink, new_canvas


def test_preview_writes_a_png_in_display_colours(config, tmp_path):
    canvas = new_canvas(800, 480, config.palette)
    canvas.putpixel((0, 0), int(Ink.RED))

    out = tmp_path / "preview.png"
    PreviewDisplay(config, path=out).show(canvas)

    assert out.exists()
    with Image.open(out) as written:
        assert written.size == (800, 480)
        assert written.convert("RGB").getpixel((0, 0)) == config.palette.red


def test_preview_scaling_keeps_the_dither_pattern_crisp(config, tmp_path):
    canvas = new_canvas(800, 480, config.palette)
    canvas.putpixel((0, 0), int(Ink.BLACK))

    out = tmp_path / "preview.png"
    PreviewDisplay(config, path=out, scale=2).show(canvas)
    with Image.open(out) as written:
        assert written.size == (1600, 960)
        # NEAREST, not a smooth resample: no intermediate colours.
        assert written.convert("RGB").getpixel((1, 1)) == config.palette.black


def test_preview_creates_its_output_directory(config, tmp_path):
    out = tmp_path / "nested" / "dir" / "preview.png"
    PreviewDisplay(config, path=out).show(new_canvas(800, 480, config.palette))
    assert out.exists()


def test_the_preview_flag_overrides_the_configured_backend(config, tmp_path):
    from dataclasses import replace

    epaper_config = replace(config, display=replace(config.display, backend="epaper"))
    display = create_display(epaper_config, prefer_preview=True)
    assert isinstance(display, PreviewDisplay)
    assert isinstance(display, Display)


class FakeEPD:
    """Stands in for the Waveshare driver, recording the call sequence."""

    def __init__(self, fail_on_display: bool = False):
        self.width, self.height = 800, 480
        self.calls: list[str] = []
        self.buffer = None
        self.fail_on_display = fail_on_display

    def init(self):
        self.calls.append("init")
        return 0

    def display(self, buffer):
        self.calls.append("display")
        self.buffer = buffer
        if self.fail_on_display:
            raise RuntimeError("SPI went away mid-refresh")

    def Clear(self, color=0x11):
        self.calls.append("clear")

    def sleep(self):
        self.calls.append("sleep")


@pytest.fixture
def epaper(config, monkeypatch):
    """An EPaperDisplay wired to FakeEPD instead of real hardware."""
    from birddisplay.display import epaper as module

    fakes: list[FakeEPD] = []

    def make_display(fail_on_display: bool = False):
        fake = FakeEPD(fail_on_display=fail_on_display)
        fakes.append(fake)
        monkeypatch.setattr(
            module, "_load_driver", lambda: type("Driver", (), {"EPD": lambda: fake})
        )
        return module.EPaperDisplay(config), fake

    return make_display


def test_the_panel_is_always_put_back_to_sleep(epaper, config):
    # Leaving a Spectra 6 powered in its active state is how people
    # damage them.
    display, fake = epaper()
    display.show(new_canvas(800, 480, config.palette))
    assert fake.calls == ["init", "display", "sleep"]


def test_the_panel_sleeps_even_when_the_refresh_blows_up(epaper, config):
    display, fake = epaper(fail_on_display=True)
    with pytest.raises(RuntimeError):
        display.show(new_canvas(800, 480, config.palette))
    assert fake.calls[-1] == "sleep"


def test_closing_an_already_sleeping_panel_does_not_touch_it_again(epaper, config):
    display, fake = epaper()
    display.show(new_canvas(800, 480, config.palette))
    display.close()
    display.close()
    assert fake.calls.count("sleep") == 1


def test_the_panel_receives_a_packed_buffer_not_a_re_dithered_image(epaper, config):
    display, fake = epaper()
    display.show(new_canvas(800, 480, config.palette))
    # Two pixels per byte, and white is wire code 1 in both nibbles.
    assert len(fake.buffer) == 800 * 480 // 2
    assert set(fake.buffer) == {0x11}


def test_a_rotated_panel_still_sends_a_full_frame(config, epaper):
    from dataclasses import replace

    rotated = replace(config, display=replace(config.display, rotate=180))
    display, fake = epaper()
    display.config = rotated
    canvas = new_canvas(800, 480, config.palette)
    canvas.putpixel((0, 0), int(Ink.BLACK))
    display.show(canvas)
    assert len(fake.buffer) == 800 * 480 // 2
    # The black pixel moved to the opposite corner.
    assert fake.buffer[-1] & 0x0F == 0x0


def test_an_unpalettised_board_is_refused(epaper, config):
    display, _ = epaper()
    with pytest.raises(ValueError, match="palettised"):
        display.show(Image.new("RGB", (800, 480)))


def test_a_wrongly_sized_board_is_refused(epaper, config):
    display, _ = epaper()
    with pytest.raises(ValueError, match="panel is"):
        display.show(new_canvas(400, 240, config.palette))


def test_loading_the_driver_off_hardware_says_what_to_do(config, monkeypatch):
    from birddisplay.display import epaper as module

    def explode():
        raise ImportError("No module named 'spidev'")

    monkeypatch.setattr(module, "_load_driver", explode)
    with pytest.raises(ImportError):
        module.EPaperDisplay(config)


def test_the_real_driver_import_fails_helpfully_off_a_pi():
    from birddisplay.display.epaper import EPaperUnavailable, _load_driver

    try:
        _load_driver()
    except EPaperUnavailable as exc:
        assert "--preview" in str(exc)
    except Exception as exc:  # pragma: no cover - only on a real Pi
        pytest.skip(f"driver imported (running on hardware?): {exc}")
