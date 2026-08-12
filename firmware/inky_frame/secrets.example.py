"""Copy to secrets.py, fill in, and copy that to the frame.

secrets.py is gitignored. Nothing here is dangerous -- the URLs are public
and the wifi password only opens your own front door -- but a repository is
still the wrong place for it.
"""

WIFI_SSID = "your-network"
WIFI_PASSWORD = "your-password"

# Published by .github/workflows/board.yml every two hours. The manifest is
# a few hundred bytes and is fetched on every wake; the PNG is only fetched
# when the manifest says the sha has changed.
MANIFEST_URL = "https://raw.githubusercontent.com/edwardraper/Bird-Watcher-/board/board.json"
BOARD_URL = "https://raw.githubusercontent.com/edwardraper/Bird-Watcher-/board/board.png"
