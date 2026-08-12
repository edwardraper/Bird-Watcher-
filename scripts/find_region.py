"""One-off: find your county's eBird region code.

Run this once during setup, note the code, and hardcode it in
config.toml. Do not guess it -- Devon is GB-ENG-DEV, but plenty of
counties are not what you would assume.

    EBIRD_API_KEY=... python -m scripts.find_region --parent GB-ENG
    EBIRD_API_KEY=... python -m scripts.find_region --parent GB-ENG --match dev
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.config import load_config  # noqa: E402
from birddisplay.log import setup_logging  # noqa: E402
from birddisplay.sources.ebird import EbirdClient  # noqa: E402
from birddisplay.sources.http import HttpClient  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument(
        "--parent",
        default="GB-ENG",
        help="parent region to list subnational2 regions of (default GB-ENG)",
    )
    parser.add_argument("--match", help="case-insensitive substring filter")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    config = load_config(args.config)

    with HttpClient(config.http) as http:
        regions = EbirdClient(config, http).subnational2_list(args.parent)

    needle = (args.match or "").lower()
    for region in sorted(regions, key=lambda r: r.get("name", "")):
        name = region.get("name", "")
        if needle and needle not in name.lower() and needle not in region.get("code", "").lower():
            continue
        print(f"{region.get('code', ''):<16} {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
