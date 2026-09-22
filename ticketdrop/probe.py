# One-off diagnostic: fetch the FIXR venue endpoint once and describe what came back.
#
#   mpremote cp config.py secrets.py : + run probe.py
#
# Paste the whole output back. It also saves the raw body to probe.json on the
# frame's flash; fetch it with `mpremote cp :probe.json .` if the schema needs
# a closer look.

import gc
import json
import time

import network
import urequests

import config
import secrets

SAVE_LIMIT = 256 * 1024  # don't fill the flash with a runaway response


def connect_wifi():
    try:
        import rp2
        rp2.country(config.WIFI_COUNTRY)
    except Exception:
        pass
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    try:
        wlan.config(pm=0xA11140)  # turn off power saving; it drops long HTTPS reads
    except Exception:
        pass
    if not wlan.isconnected():
        print("wifi: connecting to", secrets.WIFI_SSID)
        wlan.connect(secrets.WIFI_SSID, secrets.WIFI_PASSWORD)
        for _ in range(30):
            if wlan.isconnected():
                break
            time.sleep(1)
    if not wlan.isconnected():
        raise RuntimeError("wifi: failed, status %s" % wlan.status())
    print("wifi: connected", wlan.ifconfig()[0])


def short(value, limit=120):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...(%d chars)" % len(text)


def describe_event(event):
    print("first event keys:", sorted(event.keys()))
    for key in sorted(event.keys()):
        value = event[key]
        print("  %s (%s) = %s" % (key, type(value).__name__, short(value)))
        if isinstance(value, dict):
            print("      nested keys:", sorted(value.keys()))
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            print("      list of %d dicts, first keys: %s" % (len(value), sorted(value[0].keys())))


def find_events(data):
    """Return (where, list) for the events array, trying 'events' then any list of dicts."""
    if isinstance(data, list):
        return "<top level list>", data
    if "events" in data and isinstance(data["events"], list):
        return "events", data["events"]
    for key in data:
        value = data[key]
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return key, value
    return None, None


def main():
    connect_wifi()
    gc.collect()
    print("mem_free before request:", gc.mem_free())

    headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    print("GET", config.VENUE_URL)
    started = time.ticks_ms()
    r = urequests.get(config.VENUE_URL, headers=headers)
    try:
        status = r.status_code
        body = r.content
        resp_headers = getattr(r, "headers", None) or {}
    finally:
        r.close()
    del r
    elapsed = time.ticks_diff(time.ticks_ms(), started)

    print("status:", status)
    print("response length (bytes):", len(body))
    print("request took (ms):", elapsed)
    for name in ("Content-Type", "Retry-After", "Server", "CF-RAY"):
        for key in resp_headers:
            if key.lower() == name.lower():
                print("header %s: %s" % (key, resp_headers[key]))

    if len(body) <= SAVE_LIMIT:
        with open("probe.json", "wb") as f:
            f.write(body)
        print("saved raw body to probe.json")

    if status != 200:
        print("NOT 200. first 400 bytes of body:")
        print(body[:400])
        return

    gc.collect()
    before = gc.mem_free()
    print("mem_free before json.loads:", before)
    try:
        data = json.loads(body)
    except MemoryError:
        print("!!! MemoryError in json.loads !!!")
        print("The whole-document parse does not fit in RAM on this board.")
        print("We need a chunked/streaming reader instead of json.loads.")
        return
    except ValueError as e:
        print("json.loads failed (not JSON?):", e)
        print("first 400 bytes of body:")
        print(body[:400])
        return
    del body
    gc.collect()
    after = gc.mem_free()
    print("mem_free after json.loads (body freed):", after)
    print("parsed object costs about %d bytes" % (before - after))

    print("top-level type:", type(data).__name__)
    if isinstance(data, dict):
        print("top-level keys:", sorted(data.keys()))
        for key in sorted(data.keys()):
            print("  %s (%s) = %s" % (key, type(data[key]).__name__, short(data[key], 80)))

    where, events = find_events(data)
    if events is None:
        print("no events array found")
        return
    print("events found under:", where)
    print("number of events:", len(events))
    if events:
        describe_event(events[0])
        print("all event ids + names:")
        for event in events:
            print("  ", event.get("id"), "|", event.get("name"))


try:
    main()
except Exception as e:
    import sys
    print("probe failed:")
    sys.print_exception(e)
