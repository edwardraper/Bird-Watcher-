"""Verify the eBird API assumptions this project is built on.

Everything here was written against the documented response shapes and
tested against recorded fixtures, but never against the live API. Run this
once from a machine that can reach api.ebird.org and it will tell you
whether those assumptions hold for your key and your region.

    export EBIRD_API_KEY=...
    python -m scripts.check_api

Each check prints PASS, FAIL or WARN and, at the end, what to do about
anything that is not PASS. The key is never printed or written anywhere.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.config import Config, ConfigError, load_config  # noqa: E402
from birddisplay.log import setup_logging  # noqa: E402
from birddisplay.sources.ebird import EbirdClient  # noqa: E402
from birddisplay.sources.http import FetchError, HttpClient  # noqa: E402
from birddisplay.sources.images import (  # noqa: E402
    GENERIC_CREDIT,
    ImageSource,
    ImageUnavailable,
)
from birddisplay.model import Species  # noqa: E402

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"

# A few British species whose eBird common name differs between locales.
# If these come back American, sppLocale is not being honoured.
LOCALE_PROBES = {
    "dunnoc1": "Dunnock",
    "eurrob1": "European Robin",
}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        marker = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn "}[status]
        print(f"[{marker}] {name}" + (f"\n           {detail}" if detail else ""))

    @property
    def failed(self) -> bool:
        return any(status == FAIL for status, _, _ in self.rows)


def check_auth(client: EbirdClient, report: Report) -> list[dict]:
    """The header name and key format, which is what a 403 would catch."""
    try:
        observations = client.recent_nearby(max_results=5)
    except FetchError as exc:
        detail = str(exc)
        if "403" in detail or "401" in detail:
            report.add(
                FAIL,
                "authentication",
                "eBird rejected the key. Check EBIRD_API_KEY, and that the key "
                "is still active at https://ebird.org/api/keygen",
            )
        else:
            report.add(FAIL, "recent observations near a point", detail)
        return []

    report.add(PASS, "authentication", "X-eBirdApiToken accepted")
    return observations


def check_recent(observations: list[dict], config: Config, report: Report) -> None:
    if not observations:
        report.add(
            WARN,
            "recent observations near a point",
            f"no sightings within {config.region.radius_km} km of "
            f"{config.region.lat},{config.region.lng} in the last "
            f"{config.region.back_days} days. Widen [region].radius_km or "
            "back_days, or check the coordinates.",
        )
        return

    required = {"speciesCode", "comName", "sciName", "locName", "obsDt"}
    missing = required - set(observations[0])
    if missing:
        report.add(
            FAIL,
            "observation record shape",
            f"expected fields are absent: {sorted(missing)}. "
            "The board builder reads these directly.",
        )
    else:
        report.add(
            PASS,
            "recent observations near a point",
            f"{len(observations)} records, e.g. {observations[0]['comName']} "
            f"at {observations[0]['locName']} ({observations[0]['obsDt']})",
        )


def check_notable(client: EbirdClient, config: Config, report: Report) -> None:
    """Also proves the region code is real -- a bad one 404s here."""
    try:
        notable = client.notable()
    except FetchError as exc:
        if "404" in str(exc):
            report.add(
                FAIL,
                "region code",
                f"eBird does not recognise {config.region.code!r}. Find the "
                "right one with: python -m scripts.find_region --parent GB-ENG",
            )
        else:
            report.add(FAIL, "notable sightings", str(exc))
        return

    report.add(PASS, "region code", f"{config.region.code} accepted")
    if notable:
        report.add(
            PASS,
            "notable sightings",
            f"{len(notable)} in the last {config.region.notable_back_days} days, "
            f"e.g. {notable[0].get('comName')}",
        )
    else:
        # Genuinely normal. The board just falls back to a common species.
        report.add(
            WARN,
            "notable sightings",
            "none reported right now, so the headline will rotate through "
            "recent species instead. Not a fault.",
        )


def check_taxonomy(client: EbirdClient, config: Config, report: Report) -> None:
    try:
        rows = client.taxonomy()
    except FetchError as exc:
        report.add(FAIL, "taxonomy download", str(exc))
        return

    if len(rows) < 10_000:
        report.add(WARN, "taxonomy download", f"only {len(rows)} rows, expected ~17k")
    else:
        report.add(PASS, "taxonomy download", f"{len(rows)} rows")

    names = {
        row.get("speciesCode"): row.get("comName")
        for row in rows
        if row.get("speciesCode") in LOCALE_PROBES
    }
    wrong = {
        code: names.get(code)
        for code, expected in LOCALE_PROBES.items()
        if names.get(code) != expected
    }
    if wrong:
        report.add(
            WARN,
            f"British common names (sppLocale={config.ebird.locale})",
            f"got {wrong}, expected {LOCALE_PROBES}. The board will still "
            "work, but with American names.",
        )
    else:
        report.add(
            PASS,
            f"British common names (sppLocale={config.ebird.locale})",
            ", ".join(sorted(LOCALE_PROBES.values())),
        )


def check_images(config: Config, http: HttpClient, report: Report) -> None:
    """Wikimedia, which is a separate host and a separate policy."""
    robin = Species("eurrob1", "European Robin", "Erithacus rubecula")
    try:
        photo = ImageSource(config, http).photo_for(robin, refresh=True)
    except (ImageUnavailable, FetchError) as exc:
        report.add(
            FAIL,
            "Wikimedia image and attribution",
            f"{exc}. The board falls back to a text-only layout, so this is "
            "survivable, but no photographs will appear.",
        )
        return

    size_kb = Path(photo.path).stat().st_size / 1024
    report.add(
        PASS,
        "Wikimedia image and attribution",
        f"{size_kb:.0f} kB, credited to {photo.credit_line or '(no credit found)'}",
    )
    if not photo.credit or photo.credit == GENERIC_CREDIT:
        report.add(
            WARN,
            "photo credit",
            f"no artist returned, only the generic {GENERIC_CREDIT!r} "
            "fallback. Most CC licences require naming the author, so this "
            "is worth chasing rather than shipping.",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument(
        "--skip-images", action="store_true", help="check eBird only"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show every request and retry"
    )
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    if not args.verbose:
        # Each failing check retries three times and would otherwise log
        # nine warnings over the top of the report it is trying to print.
        logging.getLogger("birddisplay.sources").setLevel(logging.ERROR)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"[ FAIL ] config: {exc}")
        return 2

    print(f"config:  {config.source_path}")
    print(f"region:  {config.region.name} ({config.region.code})")
    print(f"centre:  {config.region.lat}, {config.region.lng} "
          f"within {config.region.radius_km} km\n")

    report = Report()
    try:
        _ = config.ebird.api_key
    except ConfigError as exc:
        report.add(FAIL, "API key", str(exc))
        return 2

    with HttpClient(config.http) as http:
        client = EbirdClient(config, http)
        observations = check_auth(client, report)
        if observations:
            check_recent(observations, config, report)
        check_notable(client, config, report)
        check_taxonomy(client, config, report)
        if not args.skip_images:
            check_images(config, http, report)

    print()
    if report.failed:
        print("Something is wrong -- see the FAIL lines above.")
        return 1
    print("All checks passed. Run: python -m birddisplay.show --preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
