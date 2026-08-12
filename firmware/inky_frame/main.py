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

WIFI_TIMEOUT_SECONDS = 30
CHUNK = 1024

# The Pico's own flash, not the SD card. The board is tens of kilobytes and
# there is room; using flash means no card to lose and no SPI pins to get
# wrong. The SD slot stays free for whatever you want it for.
IMAGE_PATH = "/board.png"
PART_PATH = "/board.png.part"
STATE_PATH = "/last.json"


def log(message):
    # Indexed rather than unpacked: MicroPython's localtime() gives eight
    # fields and CPython's gives nine, and this file is exercised by the
    # test suite on a laptop as well as running on the board.
    now = time.localtime()
    print(
        "%04d-%02d-%02d %02d:%02d:%02d  %s"
        % (now[0], now[1], now[2], now[3], now[4], now[5], message)
    )


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


def connect_wifi():
    """Join the network, or give up after WIFI_TIMEOUT_SECONDS."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        inky_frame.led_wifi.on()
        wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
        deadline = time.ticks_add(time.ticks_ms(), WIFI_TIMEOUT_SECONDS * 1000)
        while not wlan.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                log("wifi timed out")
                inky_frame.led_wifi.off()
                return None
            time.sleep(0.5)
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
    png = pngdec.PNG(graphics)
    png.open_file(path)
    # The indices in this file are already Inky pen numbers. PNG_COPY is
    # what stops anything re-dithering the text.
    png.decode(0, 0, mode=pngdec.PNG_COPY)

    inky_frame.led_busy.on()
    log("refreshing the panel (~30s)")
    graphics.update()
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
        # meaningless, and the log unreadable.
        if time.localtime()[0] < 2024:
            log("setting the clock from NTP")
            inky_frame.set_time()

        manifest = get_json(secrets.MANIFEST_URL)
        published = manifest.get("sha256", "")
        stale = hours_since(state.get("refreshed_at", 0)) >= FORCE_REFRESH_HOURS

        if published and published == state.get("sha256") and not stale and not forced:
            log("board unchanged (%s); leaving the panel alone" % published[:12])
            state["failures"] = 0
            write_state(state)
            return REFRESH_MINUTES

        if stale:
            log("no refresh in %d hours; drawing anyway" % FORCE_REFRESH_HOURS)

        download(secrets.BOARD_URL, IMAGE_PATH, manifest.get("bytes", 0))
        disconnect_wifi(wlan)
        wlan = None

        draw(IMAGE_PATH)
        state["sha256"] = published
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
