"""Entrypoint: refresh the cache. Never touches the screen.

Runs ten minutes before each scheduled render. If this fails -- wifi down,
eBird throwing 500s, Wikimedia slow -- the panel keeps showing the previous
board rather than an error or a blank wall.

    python -m birddisplay.fetch [--verbose] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .cache import load_state, record_featured, save_board, save_state
from .config import Config, ConfigError, load_config
from .log import setup_logging
from .model import Board, Feature, Sighting
from .sources.commonness import Commonness
from .sources.ebird import EbirdClient
from .sources.http import FetchError, HttpClient
from .sources.images import ImageSource, ImageUnavailable
from .sources.taxonomy import Taxonomy, ensure_taxonomy

log = logging.getLogger("birddisplay.fetch")

# How many species we are willing to try images for before giving up and
# rendering text-only. Each attempt is 2-3 Wikimedia calls.
MAX_IMAGE_ATTEMPTS = 4


def dedupe_by_species(sightings: Iterable[Sighting]) -> list[Sighting]:
    """Keep the most recent observation of each species."""
    best: dict[str, Sighting] = {}
    for sighting in sightings:
        code = sighting.species.code
        if not code:
            continue
        current = best.get(code)
        if current is None:
            best[code] = sighting
            continue
        newest = max(current, sighting, key=lambda s: s.observed_at or datetime.min)
        # The same bird can arrive from both feeds; notable is sticky.
        if (current.notable or sighting.notable) and not newest.notable:
            newest = replace(newest, notable=True)
        best[code] = newest
    return list(best.values())


def by_recency(sightings: Iterable[Sighting]) -> list[Sighting]:
    return sorted(
        sightings, key=lambda s: s.observed_at or datetime.min, reverse=True
    )


def rank_candidates(
    notable: Sequence[Sighting],
    recent: Sequence[Sighting],
    featured: Sequence[str],
    config: Config,
    seed: str | None = None,
) -> list[Sighting]:
    """Rank species for the headline slot, best first.

    A rare bird always beats a common one. Below that we rotate: anything
    featured in the last few cycles goes to the back of the queue, so the
    wall does not show a woodpigeon three days running. The shuffle is
    seeded by date so re-running the fetcher on the same day is stable.
    """
    recently_featured = set(featured)

    ranked_notable = by_recency(notable)
    rotating = list(recent)
    random.Random(seed or datetime.now().strftime("%Y-%m-%d")).shuffle(rotating)

    ordered = (ranked_notable + rotating) if config.board.prefer_notable else (
        rotating + ranked_notable
    )

    fresh = [s for s in ordered if s.species.code not in recently_featured]
    # Least recently featured last, so a fully-seen list still rotates.
    stale = sorted(
        (s for s in ordered if s.species.code in recently_featured),
        key=lambda s: featured.index(s.species.code),
        reverse=True,
    )
    return fresh + stale


def _pick_headline(
    candidates: Sequence[Sighting], images: ImageSource | None
) -> tuple[Sighting | None, Any]:
    """Take the best candidate we can actually illustrate.

    Falling through to the next species is better than a text-only board,
    and a text-only board is better than no board.
    """
    if not candidates:
        return None, None
    if images is None:
        return candidates[0], None

    for sighting in candidates[:MAX_IMAGE_ATTEMPTS]:
        try:
            photo = images.photo_for(sighting.species)
        except (ImageUnavailable, FetchError) as exc:
            log.info("no image for %s: %s", sighting.species.common_name, exc)
            continue
        return sighting, photo

    log.warning("no image found for any candidate; board will be text-only")
    return candidates[0], None


def resolve_alternates(
    sightings: Sequence[Sighting], images: ImageSource | None
) -> list[Feature]:
    """Find a picture and a sentence for each of the other sightings.

    These become whole boards of their own, so that a button on the frame
    can page from the headline to the next bird. The frame renders
    nothing -- it copies a finished PNG into its framebuffer -- so every
    board it can be asked for has to have been drawn by the workflow
    hours earlier.

    A bird with no usable picture is dropped rather than carried: an
    alternate exists to be looked at, and a board with a name and no bird
    is not worth a button press. Failing to find one is ordinary and not
    an error -- most of these are commoner species with no plate, and the
    photograph is whatever Wikipedia happens to have.
    """
    if images is None:
        return []
    features: list[Feature] = []
    for sighting in sightings:
        try:
            photo = images.photo_for(sighting.species)
        except (ImageUnavailable, FetchError) as exc:
            log.info("no picture for %s: %s", sighting.species.common_name, exc)
            continue
        description = ""
        try:
            description = images.description_for(sighting.species)
        except Exception:  # noqa: BLE001 - a missing sentence is not a failure
            log.info("no description for %s", sighting.species.common_name)
        features.append(
            Feature(sighting=sighting, photo=photo, description=description)
        )
    log.info("resolved %d alternate board(s)", len(features))
    return features


def build_board(
    config: Config,
    taxonomy: Taxonomy,
    notable_raw: Sequence[dict[str, Any]],
    recent_raw: Sequence[dict[str, Any]],
    images: ImageSource | None,
    featured: Sequence[str],
) -> tuple[Board, Sighting | None]:
    notable = taxonomy.enrich_all(
        Sighting.from_ebird(obs, notable=True) for obs in notable_raw
    )
    recent = taxonomy.enrich_all(Sighting.from_ebird(obs) for obs in recent_raw)

    # Species-level dedupe across both feeds, so a notable bird does not
    # also appear in the digest list underneath itself.
    notable = by_recency(dedupe_by_species(notable))
    recent = by_recency(dedupe_by_species(recent))

    candidates = rank_candidates(notable, recent, featured, config)
    headline, photo = _pick_headline(candidates, images)

    headline_code = headline.species.code if headline else None
    also_seen = [s for s in recent if s.species.code != headline_code]

    # Rarest first. The board shows only a handful of these, so which few
    # it shows matters more than their order: a Yellowhammer is worth the
    # slot that a Woodpigeon was taking.
    commonness = Commonness.load()
    if commonness:
        also_seen = commonness.rarest_first(also_seen)

    description = ""
    if headline is not None and images is not None:
        try:
            description = images.description_for(headline.species)
        except Exception:  # noqa: BLE001 - a missing sentence is not a failed fetch
            log.exception("could not fetch a description for %s", headline.species.code)

    alternates = resolve_alternates(
        also_seen[: config.board.also_seen_count], images
    )

    region = config.region
    note = (
        f"{len(recent)} species within {region.radius_km} km "
        f"in the last {region.back_days} days"
    )

    board = Board(
        generated_at=datetime.now(timezone.utc),
        region_name=region.name,
        headline=headline,
        headline_photo=photo,
        also_seen=also_seen[: max(config.board.also_seen_count * 2, 16)],
        species_count=len(recent),
        checklist_note=note,
        headline_description=description,
        alternates=alternates,
    )
    return board, headline


def run(config: Config, dry_run: bool = False, force_taxonomy: bool = False) -> int:
    with HttpClient(config.http) as http:
        client = EbirdClient(config, http)
        taxonomy = ensure_taxonomy(
            client,
            config.cache.taxonomy_path,
            config.ebird.taxonomy_max_age_days,
            force=force_taxonomy,
        )

        notable_raw: list[dict[str, Any]] = []
        try:
            notable_raw = client.notable()
        except FetchError as exc:
            # Not fatal: a board with no rare bird is still a board.
            log.warning("notable sightings unavailable: %s", exc)

        recent_raw = client.recent_nearby()

        images = ImageSource(config, http)
        state = load_state(config.cache.state_path)
        board, headline = build_board(
            config, taxonomy, notable_raw, recent_raw, images, state.get("featured", [])
        )

    if dry_run:
        import json

        json.dump(board.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    save_board(config.cache.board_path, board)
    if headline is not None:
        record_featured(state, headline.species.code, config.board.feature_memory)
        save_state(config.cache.state_path, state)

    log.info(
        "board ready: headline=%s, %d species nearby",
        headline.species.common_name if headline else "(none)",
        board.species_count,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the bird board cache.")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the board payload to stdout instead of writing the cache",
    )
    parser.add_argument(
        "--force-taxonomy",
        action="store_true",
        help="refetch the eBird taxonomy even if the cache is fresh",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        return run(config, dry_run=args.dry_run, force_taxonomy=args.force_taxonomy)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    except FetchError as exc:
        # Exit non-zero so `systemctl status` shows the failure, but leave
        # the previous cache untouched for the renderer.
        log.error("fetch failed, keeping the previous cache: %s", exc)
        return 1
    except Exception:  # noqa: BLE001 - last line of defence for a cron job
        log.exception("unexpected failure; previous cache left in place")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
