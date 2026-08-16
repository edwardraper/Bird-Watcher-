"""Copy to secrets.py, fill in, and copy that to the frame.

secrets.py is gitignored. Nothing here is dangerous -- the URLs are public
and the wifi password only opens your own front door -- but a repository is
still the wrong place for it.
"""

# Exactly as the radio hears it. Access point names can carry leading or
# trailing spaces and nothing renders them -- not the router's app, not
# the wifi menu -- so a network called "Home " looks identical to "Home"
# everywhere a person can look. The frame recovers from that by itself
# now, and logs when it does; if you want to check, print repr() of the
# ssid field from network.WLAN(network.STA_IF).scan().
WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"

# Published by .github/workflows/board.yml every two hours. The manifest is
# a few hundred bytes and is fetched on every wake; the PNG is only fetched
# when the manifest says the sha has changed.
MANIFEST_URL = "https://raw.githubusercontent.com/edwardraper/Bird-Watcher-/board/board.json"
BOARD_URL = "https://raw.githubusercontent.com/edwardraper/Bird-Watcher-/board/board.png"
