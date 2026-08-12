"""How ordinary a bird is, for ordering the other sightings by rarity.

eBird tells us what was seen, not how remarkable seeing it was. The
notable feed is the only rarity signal it gives directly, and it is a
yes/no about county-level rarities -- no help at all for ordering a
Woodpigeon against a Yellowhammer, both of which are simply "not rare".

`data/uk_birds_top200.json` already ranks 200 British birds by how often
they are reported, which is the same question upside down: rank 1 is the
most ordinary bird there is, and rank 200 is the least. So rarity is just
that list read backwards, and a species absent from it is rarer than
anything on it.

Optional in every direction. A missing or malformed file leaves the board
ordering its sightings the way it always did, by recency.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..model import Species

log = logging.getLogger(__name__)

# Repository layout, not an installed path: the list is data the project
# ships, sitting beside config.toml rather than inside the package.
DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "uk_birds_top200.json"

# A bird nobody has ranked is rarer than the two hundredth. Sorting puts
# it first, which is the right guess: the list covers the common birds,
# so falling off the end of it means uncommon.
UNRANKED = 10_000


@dataclass(frozen=True)
class Commonness:
    """Commonness rank by lowercased common and scientific name."""

    ranks: dict[str, int]

    @classmethod
    def load(cls, path: Path | None = None) -> "Commonness":
        path = Path(path or DEFAULT_PATH)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entries = payload["species"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Never worth a blank wall. Without this the board still
            # renders; it just orders the list by recency as before.
            log.info("no commonness list at %s (%s); ordering by recency", path, exc)
            return cls(ranks={})

        ranks: dict[str, int] = {}
        for entry in entries:
            try:
                rank = int(entry["rank"])
            except (KeyError, ValueError, TypeError):
                continue
            for key in ("common_name", "scientific_name"):
                name = str(entry.get(key) or "").strip().lower()
                if name:
                    ranks.setdefault(name, rank)
            for synonym in entry.get("synonyms") or ():
                name = str(synonym or "").strip().lower()
                if name:
                    ranks.setdefault(name, rank)
        log.debug("loaded %d commonness names from %s", len(ranks), path)
        return cls(ranks=ranks)

    def __bool__(self) -> bool:
        return bool(self.ranks)

    def rank_of(self, species: Species) -> int:
        """Commonness rank, or UNRANKED when the list has never heard of it."""
        for name in (species.common_name, species.scientific_name):
            rank = self.ranks.get(str(name or "").strip().lower())
            if rank is not None:
                return rank
        return UNRANKED

    def rarest_first(self, sightings):
        """Order sightings from least ordinary to most.

        Stable, so sightings of equally-ranked birds keep the order they
        arrived in, which is by recency.
        """
        return sorted(sightings, key=lambda s: -self.rank_of(s.species))
