from __future__ import annotations

import pytest
from PIL import Image

from birddisplay.display.base import Display, create_display
from birddisplay.display.preview import PreviewDisplay
from birddisplay.render.palette import Ink, new_canvas

PANEL_BYTES = 800 * 480 // 2  # two pixels per byte, whatever way up it hangs


def board_canvas(config) -> Image.Image:
    """A blank board the shape the renderer composes for this config.

    Not the shape of the glass: with the frame hung on its side the board
    is 480x800 and the panel is still 800x480, and the rotation between
    them is the display backend's job. Ask the config rather than writing
    either pair down, so these stay about the panel and not about which
    way up the frame currently hangs.
    """
    return new_canvas(*config.display.board_size, config.palette)


def test_preview_writes_a_png_in_display_colours(config, tmp_path):
    canvas = board_canvas(config)
    canvas.putpixel((0, 0), int(Ink.RED))

    out = tmp_path / "preview.png"
    PreviewDisplay(config, path=out).show(canvas)

    assert out.exists()
    with Image.open(out) as written:
        # The preview shows the board as composed, not as hung: it is for
        # looking at on a laptop, where nothing is on its side.
        assert written.size == config.display.board_size
        assert written.convert("RGB").getpixel((0, 0)) == config.palette.red


def test_preview_scaling_keeps_the_dither_pattern_crisp(config, tmp_path):
    canvas = board_canvas(config)
    canvas.putpixel((0, 0), int(Ink.BLACK))

    width, height = config.display.board_size
    out = tmp_path / "preview.png"
    PreviewDisplay(config, path=out, scale=2).show(canvas)
    with Image.open(out) as written:
        assert written.size == (width * 2, height * 2)
        # NEAREST, not a smooth resample: no intermediate colours.
        assert written.convert("RGB").getpixel((1, 1)) == config.palette.black


def test_preview_creates_its_output_directory(config, tmp_path):
    out = tmp_path / "nested" / "dir" / "preview.png"
    PreviewDisplay(config, path=out).show(board_canvas(config))
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
    display.show(board_canvas(config))
    assert fake.calls == ["init", "display", "sleep"]


def test_the_panel_sleeps_even_when_the_refresh_blows_up(epaper, config):
    display, fake = epaper(fail_on_display=True)
    with pytest.raises(RuntimeError):
        display.show(board_canvas(config))
    assert fake.calls[-1] == "sleep"


def test_closing_an_already_sleeping_panel_does_not_touch_it_again(epaper, config):
    display, fake = epaper()
    display.show(board_canvas(config))
    display.close()
    display.close()
    assert fake.calls.count("sleep") == 1


def test_the_panel_receives_a_packed_buffer_not_a_re_dithered_image(epaper, config):
    display, fake = epaper()
    display.show(board_canvas(config))
    # Two pixels per byte, and white is wire code 1 in both nibbles.
    assert len(fake.buffer) == PANEL_BYTES
    assert set(fake.buffer) == {0x11}


def test_a_rotated_panel_still_sends_a_full_frame(config, epaper):
    from dataclasses import replace

    rotated = replace(config, display=replace(config.display, rotate=180))
    display, fake = epaper()
    display.config = rotated
    canvas = board_canvas(rotated)
    canvas.putpixel((0, 0), int(Ink.BLACK))
    display.show(canvas)
    assert len(fake.buffer) == PANEL_BYTES
    # The black pixel moved to the opposite corner.
    assert fake.buffer[-1] & 0x0F == 0x0


def test_a_portrait_board_arrives_at_the_panel_the_right_way_round(config, epaper):
    """The whole point of composing 480x800: the frame hangs on its side.

    A board that is merely the right number of bytes is not enough -- a
    rotation dropped somewhere upstream would still fill the buffer, just
    with the bird lying down.
    """
    from dataclasses import replace

    portrait = replace(config, display=replace(config.display, rotate=90))
    display, fake = epaper()
    display.config = portrait
    canvas = new_canvas(*portrait.display.board_size, config.palette)
    assert canvas.size == (480, 800)
    # Top-left of the composed board; ROTATE_90 carries it to bottom-left.
    canvas.putpixel((0, 0), int(Ink.BLACK))
    display.show(canvas)
    assert len(fake.buffer) == PANEL_BYTES
    first_byte_of_last_row = fake.buffer[(480 - 1) * 800 // 2]
    assert first_byte_of_last_row >> 4 == 0x0


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
