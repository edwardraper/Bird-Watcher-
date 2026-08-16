"""What runs on the Inky Frame: wake, ask, maybe draw, sleep.

This is the whole of the device half of the project, and it is deliberately
thin. Every decision about what the board says -- which bird, which plate,
which ink each pixel gets -- was made two hours ago by a GitHub Actions
runner with the eBird API, a 7MB plate database and Pillow. None of that
fits on a Pico 2 W, and none of it needs to.

So the frame does four things:

    1. wake up on the RTC alarm
    2. download a few hundred bytes of manifest and ask "changed?"
    3. if it has, download an 800x480 PNG and put it on the glass
    4. sleep for two hours at about 20 microamps

Three rules, each of which exists because this hangs on a wall:

**Never refresh the panel unless the picture changed.** A Spectra 6 refresh
takes thirty seconds, drives the panel hard and costs real battery. Most
two-hour windows bring no change worth the ink -- birds are not a news
feed -- so the manifest's sha decides, and a quiet cycle costs one small
HTTPS GET. The exception is a daily refresh even when nothing changed,
which is what keeps ghosting from setting in.

**Any failure means the glass is left alone.** No wifi, no DNS, a 404, a
truncated download, a PNG that will not decode: log it, sleep, try again.
An e-ink panel with no power keeps its last image indefinitely, so the
worst case is a board that is a few hours stale -- which is a far better
wall decoration than an error message.

**Decode with PNG_COPY.** The board arrives as a palettised PNG whose
indices are already Inky pen numbers, and PNG_COPY copies them straight
into the framebuffer. Anything else -- a JPEG, or PNG_DITHER, or
PNG_POSTERISE -- would re-dither type that was drawn at 11px to be read
from across a room, and turn the digest into mush.
"""

import gc
import json
import os
import time

import inky_frame
import network
import pngdec
from picographics import DISPLAY_INKY_FRAME_7, PicoGraphics
from urllib import urequest

import secrets

# How long to sleep between checks. The published board is rendered every
# two hours; there is no point being quicker.
REFRESH_MINUTES = 120
# After a failure. Short enough to catch a router reboot, long enough not
# to flatten the battery retrying into a dead network.
RETRY_MINUTES = 15
MAX_RETRIES = 3
# Refresh at least this often even when nothing has changed. Waveshare's
# advice for keeping ghosting out of a panel that sits still for days.
FORCE_REFRESH_HOURS = 24

# Redraw on every wake, even when the board has not changed.
#
# Off by default because the sha comparison is what makes a battery
# display worth having: a quiet cycle is a few seconds of radio, and a
# drawing cycle is thirty seconds of panel at the highest current the
# board ever draws. On costs materially more battery -- still months
# rather than days, but fewer months.
#
# Worth turning on if you would rather the wall be certainly right than
# certainly efficient. It makes the panel's contents follow from the last
# wake rather than from a record of what was drawn hours ago, which no
# amount of bookkeeping can fully guarantee: the frame cannot see its own
# glass. Set ALWAYS_REDRAW = True in secrets.py to enable it without
# editing this file.
ALWAYS_REDRAW = False

WIFI_TIMEOUT_SECONDS = 30

# What counts as a believable clock. Below this the RTC has never been
# set; above it, it has been set to nonsense. Both mean the same thing --
# ask NTP -- and only the lower bound used to be checked.
MIN_YEAR = 2024
MAX_YEAR = 2075
# How long to let the panel rest after a refresh before the power can be
# cut. Long enough for a Spectra 6 to finish settling, short enough to be
# nothing against a two-hour cycle.
PANEL_SETTLE_SECONDS = 10

# Wipe the panel to white and let it rest before drawing the board.
#
# The suspicion this exists to test: that a refresh over an image already
# on the glass is what fails, and that a panel taken to white first will
# take the board. Plenty of e-ink examples clear before drawing for
# exactly this reason.
#
# It is not free. Two full refreshes every cycle rather than one, so
# roughly double the panel wear and double the current spent drawing,
# and a cycle that takes two minutes instead of forty seconds. Worth it
# while the fault is unexplained; worth turning off once it is not.
CLEAR_BEFORE_DRAW = True

# How long to leave the panel white before drawing over it. Deliberately
# generous -- the point is to give it every chance to finish, not to be
# quick.
CLEAR_SETTLE_SECONDS = 60

# The white pen, in the order measured on this panel: 0 black, 1 white.
WHITE_PEN = 1

CHUNK = 1024

# The Pico's own flash, not the SD card. The board is tens of kilobytes and
# there is room; using flash means no card to lose and no SPI pins to get
# wrong. The SD slot stays free for whatever you want it for.
IMAGE_PATH = "/board.png"
PART_PATH = "/board.png.part"
STATE_PATH = "/last.json"

# The log, kept on flash so unattended cycles can be read back later.
# Two files rather than one: rotating rather than truncating means a
# fault is never lost to the rotation that happened just after it.
LOG_PATH = "/log.txt"
LOG_PREVIOUS_PATH = "/log.old.txt"
LOG_MAX_BYTES = 8192


def log(message):
    # Indexed rather than unpacked: MicroPython's localtime() gives eight
    # fields and CPython's gives nine, and this file is exercised by the
    # test suite on a laptop as well as running on the board.
    now = time.localtime()
    line = "%04d-%02d-%02d %02d:%02d:%02d  %s" % (
        now[0], now[1], now[2], now[3], now[4], now[5], message,
    )
    print(line)
    _append_to_log(line)


def _append_to_log(line):
    """Keep the log on flash, because the interesting cycles are unwatched.

    sleep_for() cuts the board's power, which drops the USB serial link,
    so a console can only ever watch a cycle run by hand -- and a fault
    that appears only when the frame runs on its own is then invisible by
    construction. Every log we had of this display came from the one case
    that worked.

    Written to flash it survives the power cut, and the next time anyone
    plugs in they can read what the unattended cycles actually did.
    """
    try:
        try:
            if os.stat(LOG_PATH)[6] > LOG_MAX_BYTES:
                os.rename(LOG_PATH, LOG_PREVIOUS_PATH)
        except OSError:
            pass
        with open(LOG_PATH, "a") as handle:
            handle.write(line + "\n")
    except OSError:
        # A log that cannot be written must never take the board down.
        pass


# -- state ------------------------------------------------------------


def read_state():
    """What we showed last time, and when."""
    try:
        with open(STATE_PATH) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"sha256": "", "refreshed_at": 0, "failures": 0}


def write_state(state):
    try:
        with open(STATE_PATH, "w") as handle:
            json.dump(state, handle)
    except OSError as exc:
        # Losing this costs one unnecessary refresh, nothing more.
        log("could not write state: %s" % exc)


# -- network ----------------------------------------------------------


# MicroPython's status codes, which say which failure this was. Without
# them every wifi problem looks identical from the far side of the room:
# a wrong password, a 5GHz-only network and a router that never answered
# all produce the same thirty seconds of nothing.
WIFI_STATUS = {
    0: "idle",
    1: "still connecting",
    -1: "connection failed",
    -2: "no access point with that name -- check spelling, and that it is "
        "2.4GHz: the Pico 2 W has no 5GHz radio",
    -3: "wrong password",
}


def wifi_reason(wlan):
    """Why the join failed, as a phrase to put after "wifi timed out"."""
    try:
        code = wlan.status()
    except Exception:  # pragma: no cover - older firmware may not have it
        return ""
    return ": " + WIFI_STATUS.get(code, "status %s" % code)


def matching_ssid(wlan, wanted):
    """The broadcast SSID that `wanted` was meant to be, or None.

    Access point names can carry leading or trailing spaces, and nothing
    shows them: not the router's own app, not the wifi menu, not the join
    dialog. A network called "Thorpe Grange " reads as "Thorpe Grange"
    everywhere a person can see it, and the frame then reports, quite
    correctly, that there is no access point with that name.

    So when an exact join fails, look for a name that differs only in
    whitespace and use its real bytes. Only ever consulted after a
    failure, so a network that simply is not there still fails.
    """
    try:
        found = wlan.scan()
    except Exception:  # pragma: no cover - radio busy or off
        return None
    target = wanted.strip()
    if not target:
        return None
    for entry in found:
        raw = entry[0]
        try:
            name = raw.decode()
        except Exception:
            continue
        if name != wanted and name.strip() == target:
            return raw
    return None


def join(wlan, ssid, password):
    """Ask the radio to join, and wait. True if it got there."""
    wlan.connect(ssid, password)
    deadline = time.ticks_add(time.ticks_ms(), WIFI_TIMEOUT_SECONDS * 1000)
    while not wlan.isconnected():
        if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
            return False
        time.sleep(0.5)
    return True


def connect_wifi():
    """Join the network, or give up after WIFI_TIMEOUT_SECONDS."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        inky_frame.led_wifi.on()
        if not join(wlan, secrets.WIFI_SSID, secrets.WIFI_PASSWORD):
            log("wifi timed out%s" % wifi_reason(wlan))
            corrected = matching_ssid(wlan, secrets.WIFI_SSID)
            if corrected is None:
                inky_frame.led_wifi.off()
                return None
            # Worth saying out loud: the difference is invisible, and
            # anyone reading this log will otherwise see two identical
            # names and disbelieve the first failure.
            log("retrying as %r -- the name is not what it looks like" % corrected)
            if not join(wlan, corrected, secrets.WIFI_PASSWORD):
                log("wifi timed out%s" % wifi_reason(wlan))
                inky_frame.led_wifi.off()
                return None
    inky_frame.led_wifi.off()
    log("wifi up: %s" % wlan.ifconfig()[0])
    return wlan


def disconnect_wifi(wlan):
    """Radio off before the refresh: it is the second biggest current draw
    on the board and it has nothing left to do."""
    if wlan is None:
        return
    try:
        wlan.disconnect()
        wlan.active(False)
    except OSError:
        pass


def get_json(url):
    socket = urequest.urlopen(url)
    try:
        return json.load(socket)
    finally:
        socket.close()


def download(url, path, expected_bytes=0):
    """Stream a file to flash, and refuse to keep a short one.

    Written to a .part and renamed, so a download cut off halfway can
    never be handed to the decoder as if it were a board.
    """
    socket = urequest.urlopen(url)
    written = 0
    try:
        with open(PART_PATH, "wb") as handle:
            while True:
                chunk = socket.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
    finally:
        socket.close()

    if written == 0:
        raise OSError("empty download")
    if expected_bytes and written != expected_bytes:
        raise OSError("expected %d bytes, got %d" % (expected_bytes, written))

    try:
        os.remove(path)
    except OSError:
        pass
    os.rename(PART_PATH, path)
    log("downloaded %d bytes" % written)


# -- the panel --------------------------------------------------------


def draw(path):
    """Put the downloaded board on the glass.

    PicoGraphics is created here rather than at import time because it
    reserves the framebuffer, and the TLS handshake above wants the room.
    """
    gc.collect()
    graphics = PicoGraphics(DISPLAY_INKY_FRAME_7)

    if CLEAR_BEFORE_DRAW:
        inky_frame.led_busy.on()
        log("clearing the panel first (~30s)")
        graphics.set_pen(WHITE_PEN)
        graphics.clear()
        graphics.update()
        log("panel cleared; resting %ds" % CLEAR_SETTLE_SECONDS)
        time.sleep(CLEAR_SETTLE_SECONDS)
        inky_frame.led_busy.off()

    # Read after the clear, not before it: the file has been on flash
    # since the download, and opening it again here means the board is
    # decoded onto a panel that has just been wiped rather than onto a
    # framebuffer prepared a minute and a half ago.
    png = pngdec.PNG(graphics)
    png.open_file(path)
    # The indices in this file are already Inky pen numbers. PNG_COPY is
    # what stops anything re-dithering the text.
    png.decode(0, 0, mode=pngdec.PNG_COPY)

    inky_frame.led_busy.on()
    log("refreshing the panel (~30s)")
    graphics.update()

    # Let the panel settle before anything cuts its power.
    #
    # update() returns when the waveform has been sent, and the very next
    # thing this firmware does is sleep_for(), which switches the board
    # off at the RTC. A Spectra 6 needs a moment after the last waveform
    # for the particles to come to rest; pull the supply inside that
    # window and the image never sets, which looks from across the room
    # like a refresh that completed and drew nothing.
    #
    # Cheap insurance either way: seconds once every two hours, against
    # the one failure that leaves a blank wall while every log line says
    # the refresh succeeded.
    time.sleep(PANEL_SETTLE_SECONDS)

    inky_frame.led_busy.off()
    log("panel refreshed")


# -- one wake cycle ---------------------------------------------------


def hours_since(when):
    if not when:
        return FORCE_REFRESH_HOURS + 1
    return (time.time() - when) / 3600.0


def cycle():
    """Do one wake-up's worth of work. Returns minutes to sleep for."""
    inky_frame.pcf_to_pico_rtc()
    state = read_state()
    forced = inky_frame.woken_by_button()
    if forced:
        log("woken by a button; refreshing whatever the sha says")

    wlan = None
    try:
        wlan = connect_wifi()
        if wlan is None:
            raise OSError("no network")

        # A clock that has never been set makes the 24-hour rule
        # meaningless and the log unreadable. An unset RTC does not
        # always read as 2000: the real frame came up believing it was
        # 2082, sailed past a "has it been set yet" test that only looked
        # downwards, and never asked NTP at all. A clock can be wrong in
        # either direction, so the test is whether it is plausible.
        year = time.localtime()[0]
        if not (MIN_YEAR <= year <= MAX_YEAR):
            log("clock says %d; setting it from NTP" % year)
            inky_frame.set_time()

        manifest = get_json(secrets.MANIFEST_URL)
        published = manifest.get("sha256", "")
        stale = hours_since(state.get("refreshed_at", 0)) >= FORCE_REFRESH_HOURS

        # A refresh that began and never reported finishing. The panel is
        # then whatever thirty seconds of interrupted waveform left behind
        # -- usually blank -- and no sha describes it, so the only honest
        # thing is to draw again.
        interrupted = bool(state.get("drawing"))
        if interrupted:
            log("last refresh did not finish; drawing again")

        always = getattr(secrets, "ALWAYS_REDRAW", ALWAYS_REDRAW)
        if always:
            log("ALWAYS_REDRAW is on; drawing whatever the sha says")

        if (
            published
            and published == state.get("sha256")
            and not stale
            and not forced
            and not interrupted
            and not always
        ):
            log("board unchanged (%s); leaving the panel alone" % published[:12])
            state["failures"] = 0
            write_state(state)
            return REFRESH_MINUTES

        if stale:
            log("no refresh in %d hours; drawing anyway" % FORCE_REFRESH_HOURS)

        download(secrets.BOARD_URL, IMAGE_PATH, manifest.get("bytes", 0))
        disconnect_wifi(wlan)
        wlan = None

        # Written before the panel is touched, cleared after. The frame
        # cannot look at its own glass, so this is the only way it can
        # ever know that a refresh started and did not come back: the
        # thirty seconds of a Spectra 6 update are the likeliest moment
        # for a battery to sag or a cable to be pulled, and what that
        # leaves on the wall is a blank board that matches no sha at all.
        state["drawing"] = published
        write_state(state)

        draw(IMAGE_PATH)

        state["sha256"] = published
        state["drawing"] = ""
        state["refreshed_at"] = time.time()
        state["failures"] = 0
        write_state(state)
        log("showing: %s" % manifest.get("headline", "?"))
        return REFRESH_MINUTES

    except Exception as exc:  # noqa: BLE001 - a wall display must not stop
        # Deliberately broad: whatever went wrong, the answer is the same.
        # The panel keeps the board it already has, which is the whole
        # reason this is safe to leave alone for a week.
        failures = state.get("failures", 0) + 1
        state["failures"] = failures
        write_state(state)
        log("cycle failed (%d/%d): %s" % (failures, MAX_RETRIES, exc))
        if failures >= MAX_RETRIES:
            log("giving up until the next scheduled wake")
            state["failures"] = 0
            write_state(state)
            return REFRESH_MINUTES
        return RETRY_MINUTES
    finally:
        disconnect_wifi(wlan)
        gc.collect()


def sleep(minutes):
    """Cut the power until the RTC wakes us.

    On battery this returns nothing -- the board is off. On USB the RTC
    cannot cut power, so sleep_for falls through and we wait the interval
    out instead, which makes the same code work on a bench.
    """
    log("sleeping for %d minutes" % minutes)
    inky_frame.sleep_for(minutes)
    time.sleep(minutes * 60)


def main():
    while True:
        sleep(cycle())


if __name__ == "__main__":
    main()
