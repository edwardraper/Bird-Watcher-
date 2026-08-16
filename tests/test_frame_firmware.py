"""The frame's decision logic, run on a laptop.

main.py runs on a Pico 2 W under MicroPython, so none of it can be
imported here without help. What can be tested is the part that actually
matters and is easy to get wrong: when does it refresh the panel?

Getting that wrong is expensive in both directions. Refresh too eagerly
and a battery display drives a thirty-second panel update every two hours
forever; refresh too shyly and the wall quietly stops being true. So the
hardware, the network and the decoder are stubbed, and these tests ask
only: given this manifest and this saved state, did it draw?
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

FIRMWARE = Path(__file__).resolve().parent.parent / "firmware" / "inky_frame" / "main.py"


class FakeLed:
    def __init__(self) -> None:
        self.on_count = 0

    def on(self) -> None:
        self.on_count += 1

    def off(self) -> None:
        pass


class FakeInkyFrame(types.ModuleType):
    """Stands in for the board: LEDs, the RTC, and the power cut."""

    def __init__(self) -> None:
        super().__init__("inky_frame")
        self.led_wifi = FakeLed()
        self.led_busy = FakeLed()
        self.slept_for: list[int] = []
        self.button = False
        self.clock_set = 0

    def pcf_to_pico_rtc(self) -> None:
        pass

    def set_time(self) -> None:
        self.clock_set += 1

    def woken_by_button(self) -> bool:
        return self.button

    def sleep_for(self, minutes: int) -> None:
        self.slept_for.append(minutes)


class FakeSocket:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class FakeNetwork:
    """A wifi radio that connects, or does not."""

    STA_IF = 0

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.status_code = -2
        # What the radio can see, as raw bytes -- the point being that a
        # name may not be spelled the way anyone thinks it is.
        self.visible: list[bytes] = [b"net"]

    def WLAN(self, _interface: int):  # noqa: N802 - matching MicroPython
        outer = self

        class _Wlan:
            def active(self, _state: bool) -> None:
                pass

            def isconnected(self) -> bool:
                return outer.connected

            def connect(self, _ssid: str, _password: str) -> None:
                pass

            def scan(self):
                return [(name, b"\x00" * 6, 4, -50, 5, False) for name in outer.visible]

            def status(self) -> int:
                # MicroPython reports why a join failed; -2 is "no access
                # point with that name", the 5GHz/typo case.
                return 3 if outer.connected else outer.status_code

            def disconnect(self) -> None:
                pass

            def ifconfig(self):
                return ("10.0.0.9", "", "", "")

        return _Wlan()


@pytest.fixture
def frame(tmp_path: Path, monkeypatch):
    """Load main.py with every hardware module replaced by a stub."""
    inky = FakeInkyFrame()
    net = FakeNetwork()

    drawn: list[str] = []
    manifest = {"sha256": "abc123", "bytes": 4, "headline": "Robin"}
    responses = {"manifest": manifest, "image": b"PNG!"}

    urequest = types.ModuleType("urequest")

    def urlopen(url: str):
        if url.endswith(".json"):
            return FakeSocket(json.dumps(responses["manifest"]).encode())
        return FakeSocket(responses["image"])

    urequest.urlopen = urlopen  # type: ignore[attr-defined]
    urllib = types.ModuleType("urllib")
    urllib.urequest = urequest  # type: ignore[attr-defined]

    pngdec = types.ModuleType("pngdec")
    pngdec.PNG_COPY = 1  # type: ignore[attr-defined]

    class FakePNG:
        def __init__(self, _graphics) -> None:
            pass

        def open_file(self, path: str) -> None:
            drawn.append(path)

        def decode(self, _x, _y, mode=None) -> None:
            assert mode == pngdec.PNG_COPY, "must not re-dither the board"

    pngdec.PNG = FakePNG  # type: ignore[attr-defined]

    picographics = types.ModuleType("picographics")
    picographics.DISPLAY_INKY_FRAME_7 = 7  # type: ignore[attr-defined]
    class FakeGraphics:
        """Records the calls in order, which is the whole point: a clear
        that happens after the decode would wipe the board."""

        def set_pen(self, pen) -> None:
            drawn.append("pen%s" % pen)

        def clear(self) -> None:
            drawn.append("clear")

        def update(self) -> None:
            drawn.append("update")

    picographics.PicoGraphics = lambda _display: FakeGraphics()  # type: ignore[attr-defined]

    secrets = types.ModuleType("secrets")
    secrets.WIFI_SSID = "net"  # type: ignore[attr-defined]
    secrets.WIFI_PASSWORD = "pw"  # type: ignore[attr-defined]
    secrets.MANIFEST_URL = "https://example.invalid/board.json"  # type: ignore[attr-defined]
    secrets.BOARD_URL = "https://example.invalid/board.png"  # type: ignore[attr-defined]

    for name, module in {
        "inky_frame": inky,
        "network": net,
        "pngdec": pngdec,
        "picographics": picographics,
        "urllib": urllib,
        "secrets": secrets,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    # MicroPython's monotonic clock. CPython has no ticks_* at all, so
    # without these the wifi timeout branch raises AttributeError instead
    # of timing out -- and every test of it passes for the wrong reason,
    # because a raised exception and a timeout leave the same wreckage.
    monkeypatch.setattr(time, "ticks_ms", lambda: int(time.time() * 1000), raising=False)
    monkeypatch.setattr(time, "ticks_add", lambda t, d: t + d, raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda a, b: a - b, raising=False)

    spec = importlib.util.spec_from_file_location("frame_main", FIRMWARE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # No test is about how long the radio is given, and with the timeout
    # branch now actually reachable the default 30s would be spent, twice
    # over, waiting for a fake radio to fail.
    module.WIFI_TIMEOUT_SECONDS = 0
    # Nor is any test about how long the panel is left to settle.
    module.PANEL_SETTLE_SECONDS = 0
    module.CLEAR_SETTLE_SECONDS = 0

    # Keep the device's writes inside tmp_path. The paths are absolute on
    # the frame, where "/" is its own flash -- on a laptop they are the
    # root of somebody's actual disk, so every one of them has to be
    # redirected here or the suite writes to the machine running it.
    module.IMAGE_PATH = str(tmp_path / "board.png")
    module.PART_PATH = str(tmp_path / "board.png.part")
    module.STATE_PATH = str(tmp_path / "last.json")
    module.LOG_PATH = str(tmp_path / "log.txt")
    module.LOG_PREVIOUS_PATH = str(tmp_path / "log.old.txt")

    return types.SimpleNamespace(
        main=module,
        inky=inky,
        net=net,
        drawn=drawn,
        responses=responses,
        state_path=Path(module.STATE_PATH),
    )


def save_state(frame, **fields) -> None:
    state = {"sha256": "", "refreshed_at": 0, "failures": 0}
    state.update(fields)
    frame.state_path.write_text(json.dumps(state))


def test_a_new_board_is_downloaded_and_drawn(frame) -> None:
    minutes = frame.main.cycle()
    assert "update" in frame.drawn
    assert minutes == frame.main.REFRESH_MINUTES
    assert json.loads(frame.state_path.read_text())["sha256"] == "abc123"


def test_an_unchanged_board_never_touches_the_panel(frame) -> None:
    """The whole reason the manifest exists: most two-hour windows bring
    no change, and a refresh costs 30 seconds of driving the panel."""
    save_state(frame, sha256="abc123", refreshed_at=time.time())
    minutes = frame.main.cycle()
    assert frame.drawn == []
    assert minutes == frame.main.REFRESH_MINUTES


def test_a_day_without_a_refresh_forces_one(frame) -> None:
    """E-ink that holds one image for a long time starts to ghost."""
    save_state(
        frame,
        sha256="abc123",
        refreshed_at=time.time() - (frame.main.FORCE_REFRESH_HOURS + 1) * 3600,
    )
    frame.main.cycle()
    assert "update" in frame.drawn


def test_a_button_press_forces_a_refresh(frame) -> None:
    save_state(frame, sha256="abc123", refreshed_at=time.time())
    frame.inky.button = True
    frame.main.cycle()
    assert "update" in frame.drawn


def test_no_network_leaves_the_panel_alone(frame) -> None:
    frame.net.connected = False
    minutes = frame.main.cycle()
    assert frame.drawn == []
    assert minutes == frame.main.RETRY_MINUTES
    assert json.loads(frame.state_path.read_text())["failures"] == 1


def test_repeated_failures_stop_retrying(frame) -> None:
    """Retrying every 15 minutes into a dead router until the battery is
    flat helps nobody."""
    frame.net.connected = False
    save_state(frame, failures=frame.main.MAX_RETRIES - 1)
    minutes = frame.main.cycle()
    assert minutes == frame.main.REFRESH_MINUTES
    assert json.loads(frame.state_path.read_text())["failures"] == 0


def test_a_truncated_download_is_not_drawn(frame) -> None:
    """The manifest says how many bytes to expect, so a connection that
    drops halfway cannot reach the decoder."""
    frame.responses["manifest"] = {"sha256": "def456", "bytes": 99999}
    minutes = frame.main.cycle()
    assert frame.drawn == []
    assert minutes == frame.main.RETRY_MINUTES


def test_a_failed_refresh_does_not_record_a_sha(frame) -> None:
    """Otherwise the next cycle would think it had already drawn it."""
    frame.responses["manifest"] = {"sha256": "def456", "bytes": 99999}
    frame.main.cycle()
    assert json.loads(frame.state_path.read_text())["sha256"] == ""


def test_the_clock_is_only_set_when_it_is_wrong(frame) -> None:
    frame.main.cycle()
    # The host clock is correct, so NTP is not needed.
    assert frame.inky.clock_set == 0


def test_a_wifi_failure_says_which_failure_it_was(frame, capsys) -> None:
    """Thirty seconds of nothing looks the same for every wifi problem.

    A wrong password, a 5GHz-only network and a router that never
    answered are one message apart on the panel's own log, and that
    message is the whole diagnosis when the frame is across the room.
    """
    frame.net.connected = False
    frame.net.status_code = -2
    frame.main.cycle()
    assert "no access point with that name" in capsys.readouterr().out


def test_a_wrong_password_says_so(frame, capsys) -> None:
    frame.net.connected = False
    frame.net.status_code = -3
    frame.main.cycle()
    assert "wrong password" in capsys.readouterr().out


def test_an_unknown_status_still_reports_the_number(frame, capsys) -> None:
    frame.net.connected = False
    frame.net.status_code = 99
    frame.main.cycle()
    assert "status 99" in capsys.readouterr().out


def test_a_trailing_space_in_the_network_name_is_recovered(frame, capsys) -> None:
    """The name in secrets.py is what a person can see; the broadcast
    name is what the radio hears, and they are not always the same.

    A real network called "Thorpe Grange " reads as "Thorpe Grange" in
    the router's app, in the wifi menu and in the join dialog. Nothing
    renders the space, so nothing can be blamed for it -- the frame just
    reports, correctly, that no such network exists.
    """
    frame.net.connected = False
    frame.net.visible = [b"Thorpe Grange "]
    frame.main.secrets.WIFI_SSID = "Thorpe Grange"

    joined: list = []

    def fake_join(wlan, ssid, password):
        joined.append(ssid)
        # The exact name fails; the bytes off the air succeed.
        connected = isinstance(ssid, bytes)
        frame.net.connected = connected
        return connected

    frame.main.join = fake_join
    assert frame.main.connect_wifi() is not None
    assert joined == ["Thorpe Grange", b"Thorpe Grange "]
    assert "not what it looks like" in capsys.readouterr().out


def test_a_network_that_is_simply_absent_still_fails(frame) -> None:
    """The whitespace rescue must not turn a missing network into a
    different one that happens to be nearby."""
    frame.net.connected = False
    frame.net.visible = [b"Somebody Else"]
    frame.main.secrets.WIFI_SSID = "Thorpe Grange"
    assert frame.main.connect_wifi() is None


def test_the_rescue_only_matches_on_whitespace(frame) -> None:
    from_air = frame.main.matching_ssid

    class Radio:
        def __init__(self, names):
            self.names = names

        def scan(self):
            return [(n, b"", 4, -50, 5, False) for n in self.names]

    assert from_air(Radio([b"Thorpe Grange "]), "Thorpe Grange") == b"Thorpe Grange "
    assert from_air(Radio([b" Thorpe Grange"]), "Thorpe Grange") == b" Thorpe Grange"
    # An exact match needs no correcting, and a different name is not one.
    assert from_air(Radio([b"Thorpe Grange"]), "Thorpe Grange") is None
    assert from_air(Radio([b"Thorpe Grange Guest"]), "Thorpe Grange") is None
    assert from_air(Radio([b""]), "Thorpe Grange") is None


def test_an_interrupted_refresh_is_drawn_again(frame) -> None:
    """The frame cannot look at its own glass.

    Thirty seconds of Spectra 6 refresh is the likeliest moment for a
    battery to sag or a cable to be pulled, and what that leaves is a
    blank panel matching no sha at all. Without this the frame compares
    shas, decides it is already showing that board, and leaves the wall
    blank until the daily forced refresh -- which is how a wall stays
    empty for a day with nothing reporting a fault.
    """
    save_state(frame, sha256="abc", refreshed_at=time.time(), drawing="abc")
    frame.responses["manifest"]["sha256"] = "abc"

    frame.main.cycle()
    assert frame.drawn, "an unfinished refresh must be redrawn"


def test_a_finished_refresh_clears_the_marker(frame) -> None:
    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.cycle()

    state = json.loads(frame.state_path.read_text())
    assert state["sha256"] == "abc"
    assert not state["drawing"], "a completed refresh must not look interrupted"


def test_the_marker_is_set_before_the_panel_is_touched(frame) -> None:
    """Set afterwards it would prove nothing: the whole point is that the
    record survives a power cut in the middle of the update."""
    seen: list = []
    real_draw = frame.main.draw

    def watching_draw(path):
        seen.append(json.loads(frame.state_path.read_text()).get("drawing"))
        return real_draw(path)

    frame.main.draw = watching_draw
    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.cycle()

    assert seen == ["abc"], "the marker must already be on disk when drawing starts"


def test_an_unchanged_board_is_still_left_alone(frame) -> None:
    """The rescue must not become "redraw every time", which would drive a
    thirty-second panel update every two hours forever."""
    save_state(frame, sha256="abc", refreshed_at=time.time(), drawing="")
    frame.responses["manifest"]["sha256"] = "abc"

    frame.main.cycle()
    assert frame.drawn == []


def test_always_redraw_draws_an_unchanged_board(frame) -> None:
    """For a wall you would rather have certainly right than efficient.

    No record of what was drawn can be fully trusted -- the frame cannot
    see its own glass -- so this makes the panel follow from the last
    wake instead.
    """
    save_state(frame, sha256="abc", refreshed_at=time.time(), drawing="")
    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.secrets.ALWAYS_REDRAW = True
    try:
        frame.main.cycle()
    finally:
        del frame.main.secrets.ALWAYS_REDRAW
    assert frame.drawn, "an unchanged board must still be drawn when asked"


def test_the_setting_is_off_unless_secrets_says_otherwise(frame) -> None:
    """A frame whose secrets.py predates the setting must not start
    refreshing the panel every two hours."""
    assert frame.main.ALWAYS_REDRAW is False
    assert not hasattr(frame.main.secrets, "ALWAYS_REDRAW")

    save_state(frame, sha256="abc", refreshed_at=time.time(), drawing="")
    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.cycle()
    assert frame.drawn == []


def test_a_clock_set_to_the_future_is_corrected(frame, monkeypatch) -> None:
    """The real frame came up believing it was 2082.

    A test that only looked downwards -- "is the year before 2024?" --
    saw 2082, decided the clock had been set, and never asked NTP. The
    24-hour refresh rule is computed from that clock, so a wrong one
    makes it meaningless in a way nothing reports.
    """
    monkeypatch.setattr(
        frame.main.time, "localtime", lambda *a: (2082, 1, 1, 8, 11, 52, 0, 1)
    )
    frame.main.cycle()
    assert frame.inky.clock_set == 1, "an implausible year must be corrected"


def test_a_plausible_clock_is_left_alone(frame, monkeypatch) -> None:
    """NTP on every wake would be a needless round trip on a board whose
    whole point is a few seconds of radio."""
    monkeypatch.setattr(
        frame.main.time, "localtime", lambda *a: (2026, 8, 16, 20, 0, 0, 5, 228)
    )
    frame.main.cycle()
    assert frame.inky.clock_set == 0


def test_the_panel_is_left_to_settle_before_the_power_can_be_cut(frame) -> None:
    """update() returns when the waveform has been sent, and the next
    thing this firmware does is switch the board off at the RTC. Pull the
    supply before the particles have come to rest and the image never
    sets -- which reads, from across the room, as a refresh that
    completed and drew nothing.
    """
    slept: list = []
    frame.main.time.sleep = lambda seconds: slept.append(seconds)

    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.cycle()

    assert frame.main.PANEL_SETTLE_SECONDS in slept, (
        "nothing waited for the panel between update() and sleep_for()"
    )


def test_the_panel_is_cleared_before_the_board_is_decoded(frame) -> None:
    """Order is the whole point.

    A clear after the decode would wipe the board off the glass, which is
    the failure this was added to rule out rather than cause. The image
    is opened after the wipe as well, so the board lands on a panel that
    has just been taken to white.
    """
    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.cycle()

    assert "clear" in frame.drawn, "the panel was never cleared"
    board = frame.main.IMAGE_PATH
    assert frame.drawn.index("clear") < frame.drawn.index(board), (
        "the clear must come before the board is decoded, not after it"
    )
    # White, not black: pen 1 on the order measured on this panel.
    assert frame.drawn[frame.drawn.index("clear") - 1] == "pen1"
    # Two refreshes now, the wipe and the board.
    assert frame.drawn.count("update") == 2


def test_clearing_can_be_turned_off(frame) -> None:
    """It costs a second full refresh every cycle, so it must be one
    constant to switch off once the fault is understood."""
    frame.main.CLEAR_BEFORE_DRAW = False
    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.cycle()

    assert "clear" not in frame.drawn
    assert frame.drawn.count("update") == 1


def test_the_log_survives_on_flash(frame) -> None:
    """The cycles worth reading are the ones nobody is watching.

    sleep_for() cuts the board's power and takes the USB serial link with
    it, so a console can only ever see a cycle run by hand -- and this
    display's fault appeared only when it ran on its own. Every log there
    was came from the one case that worked.
    """
    frame.responses["manifest"]["sha256"] = "abc"
    frame.main.cycle()

    written = Path(frame.main.LOG_PATH).read_text()
    assert "refreshing the panel" in written
    assert "showing:" in written


def test_the_log_is_rotated_rather_than_truncated(frame) -> None:
    """Truncating would lose a fault to the rotation just after it."""
    Path(frame.main.LOG_PATH).write_text("x" * (frame.main.LOG_MAX_BYTES + 1))
    frame.main.log("after the rotation")

    assert Path(frame.main.LOG_PREVIOUS_PATH).exists(), "the old log was thrown away"
    assert "after the rotation" in Path(frame.main.LOG_PATH).read_text()


def test_an_unwritable_log_never_takes_the_board_down(frame) -> None:
    """A wall display must not fail because it could not write a note
    about failing."""
    frame.main.LOG_PATH = "/nonexistent-directory/log.txt"
    frame.main.LOG_PREVIOUS_PATH = "/nonexistent-directory/log.old.txt"
    frame.main.log("this must not raise")
