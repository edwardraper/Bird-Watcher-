"""Build the Keulemans plate database the Pi reads from.

    python -m scripts.build_plate_db -v

John Gerrard Keulemans (1842-1912) drew most of the plates in Lilford's
*Coloured Figures of the Birds of the British Islands* and Dresser's *A
History of the Birds of Europe*. He died in 1912, so the lithographs are
public domain worldwide, and Wikimedia Commons has scans of nearly all of
them. Between those two works, essentially every British bird is covered.

Lithographs suit this panel far better than photographs do. Six inks and no
partial refresh punish photographic mid-tones; a lithograph is already flat
colour with a drawn outline, which is what the dithering wants. And once the
paper is cut away (see plate_cutout.py) the bird sits on the board's own
white, so it reads as a print rather than a pasted-in rectangle.

What this script does, per species:

    find candidate files on Commons  ->  score them  ->  download the best
    ->  cut the paper away  ->  encode  ->  one row in a SQLite file

The scoring is the part worth reading. Commons will happily hand you a
Persian Robin for "Erithacus rubecula", a Thorburn plate from a book
Keulemans also worked on, or a PDF of the entire volume, so each candidate
has to earn its place: evidence that Keulemans drew it, evidence that it is
the right bird, and a preference for the works with plain paper behind the
subject. Anything with no evidence of Keulemans is skipped rather than
guessed at -- a wrong credit line is worse than no plate.

Downloads are cached under --work-dir, so a re-run costs almost nothing and
Commons is not asked twice for the same file. Nothing in this module runs on
the Pi.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Sequence

import requests
from PIL import Image, UnidentifiedImageError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from birddisplay.config import ConfigError, load_config  # noqa: E402
from birddisplay.log import setup_logging  # noqa: E402
from scripts.plate_cutout import cut_out, fit_within  # noqa: E402

log = logging.getLogger("build_plate_db")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPECIES = REPO_ROOT / "data" / "uk_birds_top200.json"
DEFAULT_OUT = REPO_ROOT / "assets" / "plates" / "keulemans_uk.sqlite"
DEFAULT_WORK_DIR = Path("~/.cache/birddisplay/plate-build").expanduser()

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
EBIRD_TAXONOMY = "https://api.ebird.org/v2/ref/taxonomy/ebird"

SCHEMA_VERSION = 1
ARTIST = "J. G. Keulemans"
LICENCE = "Public domain"

# Commons hands back whole scanned volumes as well as single plates.
USABLE_MIME = {"image/jpeg", "image/png", "image/tiff"}
MIN_EDGE = 500

# The works Keulemans illustrated, best first. Lilford leads because it is
# British birds specifically and its plates sit on bare paper; Dresser is
# the same style across Europe. "Onze vogels" scores nothing extra, not for
# any doubt about the artist -- he wrote it -- but because those plates are
# full painted scenes with a landscape behind the bird, so there is no
# background to take away.
WORKS: tuple[tuple[str, str, int], ...] = (
    ("Coloured figures of the birds of the British Islands", "Lilford, Coloured Figures of the Birds of the British Islands (1885-97)", 25),
    ("A history of the birds of Europe", "Dresser, A History of the Birds of Europe (1871-81)", 20),
    ("Novitates zoologicae", "Novitates Zoologicae", 12),
    ("Ibis", "The Ibis", 12),
    ("Onze vogels", "Keulemans, Onze vogels in huis en tuin (1869)", 0),
)

# Whole volumes, title pages and bindings are scanned and uploaded next to
# the plates, and they carry the book's full metadata, so they look like
# excellent matches until you see them.
NOT_A_PLATE = ("vol.", "vol ", "volume", "title page", "frontispiece", "cover", "binding")

# Other artists who worked on the same volumes. If one of them signed the
# plate, it is not ours.
RIVAL_ARTISTS = ("thorburn", "neale", "wolf", "gronvold", "smit", "lodge", "frohawk")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_BINOMIAL_RE = re.compile(r"\b([A-Z][a-z]{3,})[\s.]+([a-z]{3,})\b")
_WORD_RE = re.compile(r"[a-z]+")


# ---------------------------------------------------------------- helpers


def _plain(raw: str | None) -> str:
    """extmetadata arrives as HTML fragments; plate text arrives as OCR."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&#039;", "'")
    text = unicodedata.normalize("NFKD", text)
    return _WS_RE.sub(" ", text).strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _has_fuzzy_word(text: str, target: str, cutoff: float = 0.78) -> bool:
    """Does `text` contain `target`, allowing for OCR damage?

    Scanned plate captions come through as things like "J.G.KeuleTnans
    libti." An exact search finds none of them; a per-word similarity
    check finds nearly all of them.
    """
    lowered = text.lower()
    if target in lowered:
        return True
    return any(
        _similar(word, target) >= cutoff
        for word in _WORD_RE.findall(lowered)
        if abs(len(word) - len(target)) <= 3
    )


def _binomials(text: str) -> list[tuple[str, str]]:
    """Every "Genus species" pair in a block of text, lowercased."""
    out = []
    for genus, epithet in _BINOMIAL_RE.findall(text):
        out.append((genus.lower(), epithet.lower()))
    # Plate captions are often set in caps: "ERITHACUS HYRCANUS".
    for match in re.finditer(r"\b([A-Z]{4,})[\s.]+([A-Z]{4,})\b", text):
        out.append((match.group(1).lower(), match.group(2).lower()))
    return out


def _filename_binomial(filename: str) -> tuple[str, str] | None:
    """The taxon a run-together filename claims to be.

    Commons is full of files called MerulaBourdilloniKeulemans.jpg, and
    that name is the most reliable thing about them -- more reliable than
    the categories, which happily put an Indian blackbird under the
    European one because they were once the same species.
    """
    stem = filename.rsplit(".", 1)[0]
    if " " in stem or "_" in stem:
        return None
    words = [w.lower() for w in re.findall(r"[A-Z][a-z]{3,}", stem)]
    words = [w for w in words if w not in {"keulemans", "smit", "wolf", "thorburn"}]
    if len(words) < 2:
        return None
    return words[0], words[1]


def _binomial_matches(found: tuple[str, str], wanted: tuple[str, str]) -> bool:
    return (
        _similar(found[0], wanted[0]) >= 0.82 and _similar(found[1], wanted[1]) >= 0.82
    )


# ------------------------------------------------------------ data models


@dataclass(frozen=True)
class SpeciesEntry:
    rank: int
    common_name: str
    scientific_name: str
    synonyms: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.scientific_name, *self.synonyms)

    def binomials(self) -> list[tuple[str, str]]:
        out = []
        for name in self.names:
            parts = name.lower().split()
            if len(parts) >= 2:
                out.append((parts[0], parts[1]))
        return out


@dataclass
class Candidate:
    title: str
    filename: str
    mime: str = ""
    width: int = 0
    height: int = 0
    description: str = ""
    artist: str = ""
    date: str = ""
    licence: str = ""
    description_url: str = ""
    thumb_url: str = ""
    categories: tuple[str, ...] = ()
    from_categories: tuple[str, ...] = ()
    score: int = 0
    evidence: list[str] = field(default_factory=list)
    work: str = ""

    @property
    def caption(self) -> str:
        """What the plate itself says: filename plus the OCR'd plate text.

        Kept apart from the categories on purpose. Commons files a Persian
        robin under *Erithacus rubecula (illustrations)*, so a binomial
        found in the categories says which drawer the file is in, while a
        binomial found in the caption says which bird was drawn.
        """
        return f"{self.title} {self.description}"

    @property
    def haystack(self) -> str:
        return " ".join((self.title, self.description, " ".join(self.categories)))


@dataclass
class PlateRecord:
    species_code: str
    rank: int
    common_name: str
    scientific_name: str
    family: str
    artist: str
    attribution: str
    work: str
    year: str
    licence: str
    source_url: str
    commons_file: str
    image: bytes
    image_format: str
    width: int
    height: int
    coverage: float


# ------------------------------------------------------------ commons API


class CommonsClient:
    """A polite, cached, read-only Commons client.

    Wikimedia's user-agent policy wants a real contact address, which the
    project already has in config.toml, and their etiquette wants serial
    requests rather than a swarm. One build is a few hundred calls.
    """

    def __init__(self, user_agent: str, work_dir: Path, delay: float = 0.35):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.work_dir = work_dir
        self.delay = delay
        self._last_call = 0.0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_call = time.monotonic()

    def query(self, **params: Any) -> dict[str, Any]:
        params.update(action="query", format="json", formatversion="2")
        for attempt in range(4):
            self._wait()
            try:
                response = self.session.get(COMMONS_API, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                wait = 2**attempt
                log.warning("commons query failed (%s); retrying in %ss", exc, wait)
                time.sleep(wait)
        log.error("commons query failed four times: %s", params)
        return {}

    def search(self, term: str, limit: int = 12) -> list[str]:
        data = self.query(list="search", srsearch=term, srnamespace="6", srlimit=limit)
        hits = (data.get("query") or {}).get("search") or []
        return [hit["title"] for hit in hits]

    def category_members(self, category: str, limit: int = 60) -> list[str]:
        data = self.query(
            list="categorymembers",
            cmtitle=f"Category:{category}",
            cmtype="file",
            cmlimit=limit,
        )
        members = (data.get("query") or {}).get("categorymembers") or []
        return [member["title"] for member in members]

    def file_info(self, titles: Sequence[str], width: int) -> dict[str, Candidate]:
        """Metadata for up to 50 files in one call."""
        out: dict[str, Candidate] = {}
        for batch_start in range(0, len(titles), 50):
            batch = titles[batch_start : batch_start + 50]
            data = self.query(
                titles="|".join(batch),
                prop="imageinfo|categories",
                iiprop="extmetadata|url|size|mime",
                iiurlwidth=width,
                cllimit="max",
            )
            for page in (data.get("query") or {}).get("pages") or []:
                if page.get("missing") or not page.get("imageinfo"):
                    continue
                info = page["imageinfo"][0]
                meta = info.get("extmetadata") or {}
                title = page["title"]
                out[title] = Candidate(
                    title=title,
                    filename=title.split(":", 1)[-1],
                    mime=info.get("mime", ""),
                    width=int(info.get("width") or 0),
                    height=int(info.get("height") or 0),
                    description=_plain(meta.get("ImageDescription", {}).get("value")),
                    artist=_plain(meta.get("Artist", {}).get("value")),
                    date=_plain(meta.get("DateTimeOriginal", {}).get("value"))[:40],
                    licence=_plain(meta.get("LicenseShortName", {}).get("value")),
                    description_url=info.get("descriptionurl", ""),
                    thumb_url=info.get("thumburl") or info.get("url", ""),
                    categories=tuple(
                        c["title"].replace("Category:", "")
                        for c in page.get("categories", [])
                    ),
                )
        return out

    def download(self, url: str, dest: Path) -> bytes:
        if dest.exists() and dest.stat().st_size > 0:
            return dest.read_bytes()
        self._wait()
        response = self.session.get(url, timeout=90)
        response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return response.content


# ------------------------------------------------------------- taxonomy


def load_taxonomy(work_dir: Path, max_age_days: int = 30) -> dict[str, dict[str, Any]]:
    """The eBird taxonomy, keyed by scientific name.

    Keyed on the same species codes the fetcher sees at runtime, which is
    what makes a plate findable from a sighting. /ref/taxonomy is the one
    eBird endpoint that serves without a key; we send one anyway if the
    environment has it.
    """
    cache = work_dir / "ebird_taxonomy.json"
    fresh = (
        cache.exists()
        and (time.time() - cache.stat().st_mtime) < max_age_days * 86400
        and cache.stat().st_size > 0
    )
    if not fresh:
        log.info("fetching the eBird taxonomy")
        headers = {"User-Agent": "birddisplay-plate-build"}
        key = os.environ.get("EBIRD_API_KEY", "").strip()
        if key:
            headers["X-eBirdApiToken"] = key
        response = requests.get(
            EBIRD_TAXONOMY,
            params={"fmt": "json", "locale": "en_UK", "cat": "species"},
            headers=headers,
            timeout=120,
        )
        response.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(response.text, encoding="utf-8")

    rows = json.loads(cache.read_text(encoding="utf-8"))
    return {row["sciName"]: row for row in rows}


def resolve_species(
    entry: SpeciesEntry, taxonomy: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Find the eBird row for a curated species.

    eBird moves birds between genera between taxonomy updates -- the
    goshawk went from Accipiter to Astur in 2024 -- so the synonyms in the
    species file are a fallback, not decoration.
    """
    for name in entry.names:
        row = taxonomy.get(name)
        if row:
            return row
    wanted = entry.common_name.lower()
    for row in taxonomy.values():
        if row["comName"].lower() == wanted:
            return row
    return None


# -------------------------------------------------------------- scoring


def score_candidate(
    entry: SpeciesEntry, candidate: Candidate, from_species_category: bool = False
) -> Candidate:
    """Decide whether this file is a Keulemans plate of this bird.

    Two questions have to be answered yes before any ranking happens: did
    Keulemans draw it, and is it this bird? Both are gates. Everything
    after that is preference -- which book, how clearly signed, how
    plate-shaped -- and preference must never outvote identity, which is
    exactly the mistake that files a Nilgiri blackbird under Turdus merula.

    Returns the candidate with `score` and `evidence` filled in. A score
    of zero or less means "do not use".
    """
    evidence: list[str] = []
    score = 0

    if candidate.mime not in USABLE_MIME:
        candidate.score = -100
        candidate.evidence = [f"unusable mime {candidate.mime or '?'}"]
        return candidate
    if min(candidate.width, candidate.height) < MIN_EDGE:
        candidate.score = -100
        candidate.evidence = [f"too small ({candidate.width}x{candidate.height})"]
        return candidate

    lowered_title = candidate.title.lower()
    if any(word in lowered_title for word in NOT_A_PLATE):
        candidate.score = -100
        candidate.evidence = ["a volume or cover, not a plate"]
        return candidate

    # -- gate one: is it Keulemans
    artist_field = candidate.artist.lower()
    signed = _has_fuzzy_word(candidate.description, "keulemans")
    sole_author = "keulemans" in artist_field and ";" not in candidate.artist
    listed = "keulemans" in artist_field
    in_artist_category = any("keulemans" in cat.lower() for cat in candidate.categories)

    if not (signed or listed or in_artist_category):
        candidate.score = -100
        candidate.evidence = ["no evidence of Keulemans"]
        return candidate

    # A rival's signature on the plate outranks a shared author list: the
    # signature is about this plate, the author list is about the book.
    if not signed:
        for rival in RIVAL_ARTISTS:
            if _has_fuzzy_word(candidate.description, rival):
                candidate.score = -100
                candidate.evidence = [f"signed by {rival.title()}"]
                return candidate

    # -- gate two: is it this bird
    wanted = entry.binomials()
    species_score = 0

    if from_species_category:
        species_score += 25
        evidence.append("listed in the species' illustrations category")

    found = _binomials(candidate.caption)
    if any(_binomial_matches(f, w) for f in found for w in wanted):
        species_score += 30
        evidence.append("caption names the species")
    elif any(
        _similar(f[0], w[0]) >= 0.85 and _similar(f[1], w[1]) < 0.82
        for f in found
        for w in wanted
    ):
        # Same genus, different epithet: a Persian Robin filed under robin.
        species_score -= 30
        evidence.append("caption names a different species in the genus")

    for cat in candidate.categories:
        lowered = cat.lower()
        for name in entry.names:
            if lowered == name.lower() or lowered.startswith(f"{name.lower()} ("):
                species_score += 20
                evidence.append("in the species' Commons category")
                break
            if lowered.startswith(f"{name.lower()} "):
                # A trinomial category: the right species, a foreign race.
                species_score += 6
                evidence.append(f"in a subspecies category ({cat})")
                break
        else:
            continue
        break

    if any(name.lower() in lowered_title for name in entry.names):
        species_score += 25
        evidence.append("filename names the species")

    claimed = _filename_binomial(candidate.filename)
    if claimed and not any(_binomial_matches(claimed, w) for w in wanted):
        # The filename names some other bird, whatever the categories say.
        species_score -= 40
        evidence.append(f"filename names {claimed[0].title()} {claimed[1]}")

    if _has_fuzzy_word(candidate.haystack, entry.common_name.split()[-1].lower()):
        species_score += 8
        evidence.append("caption names the common name")

    # A common name alone is not identification -- a Nilgiri blackbird is
    # also captioned "blackbird". Something must name the taxon.
    if species_score < 20:
        candidate.score = -100
        candidate.evidence = evidence or ["nothing ties this file to the species"]
        return candidate
    score += species_score

    # -- preference: which book, how well signed, how plate-shaped
    for needle, label, bonus in WORKS:
        if needle.lower() in candidate.haystack.lower():
            candidate.work = label
            score += bonus
            evidence.append(f"work: {label}")
            break
    else:
        score += 8

    if signed:
        score += 10
        evidence.append("signed on the plate")
    if sole_author:
        score += 8
        evidence.append("sole author on Commons")
    elif listed:
        score += 3
        evidence.append("listed among the work's artists")
    if in_artist_category:
        score += 5
        evidence.append("in a Keulemans category")

    # Plates are taller than they are wide; a wide image is usually a
    # two-page spread or a photograph of a book.
    if candidate.height >= candidate.width:
        score += 4

    candidate.score = score
    candidate.evidence = evidence
    return candidate


def candidate_titles(
    commons: CommonsClient, entry: SpeciesEntry
) -> tuple[list[str], set[str]]:
    """Gather plausible files for one species, best guesses first.

    Returns the titles and, separately, the ones that came out of the
    species' own illustrations category -- a human put those there for
    this taxon, which is the best identification evidence available for a
    plate whose caption did not survive OCR. Search comes after, because
    search will answer something for anything.
    """
    titles: list[str] = []
    seen: set[str] = set()
    from_category: set[str] = set()

    def add(items: Iterable[str], categorised: bool = False) -> None:
        for title in items:
            if categorised:
                from_category.add(title)
            if title not in seen:
                seen.add(title)
                titles.append(title)

    for name in entry.names:
        add(commons.category_members(f"{name} (illustrations)"), categorised=True)
    for name in entry.names[:2]:
        add(commons.search(f'"{name}" Keulemans'))
        add(commons.search(f'"{name}" Lilford Coloured figures'))
    return titles, from_category


# --------------------------------------------------------------- building


def encode(image: Image.Image, longest_edge: int, quality: int) -> tuple[bytes, int, int]:
    """WebP with alpha: a tenth the size of PNG for the same plate.

    Size matters here -- 200 plates ship in the repository and then to a
    Pi over someone's home wifi.
    """
    resized = fit_within(image, longest_edge)
    buffer = io.BytesIO()
    resized.save(buffer, format="WEBP", quality=quality, method=6)
    return buffer.getvalue(), resized.width, resized.height


def build_species(
    commons: CommonsClient,
    entry: SpeciesEntry,
    taxonomy: dict[str, dict[str, Any]],
    work_dir: Path,
    longest_edge: int,
    quality: int,
    request_width: int,
    max_attempts: int = 3,
) -> tuple[PlateRecord | None, dict[str, Any]]:
    """Find, cut out and encode one species' plate."""
    report: dict[str, Any] = {
        "rank": entry.rank,
        "common_name": entry.common_name,
        "scientific_name": entry.scientific_name,
    }

    row = resolve_species(entry, taxonomy)
    if row is None:
        report["outcome"] = "no eBird species code"
        return None, report
    report["species_code"] = row["speciesCode"]

    titles, from_category = candidate_titles(commons, entry)
    if not titles:
        report["outcome"] = "no candidate files on Commons"
        return None, report

    info = commons.file_info(titles, request_width)
    scored = sorted(
        (score_candidate(entry, c, c.title in from_category) for c in info.values()),
        key=lambda c: c.score,
        reverse=True,
    )
    usable = [c for c in scored if c.score > 0]
    report["candidates_seen"] = len(scored)
    if not usable:
        report["outcome"] = "no candidate passed the artist and species checks"
        report["rejected"] = [
            {"file": c.filename, "why": c.evidence[:1]} for c in scored[:3]
        ]
        return None, report

    # Only the serious contenders get downloaded. A plate that cuts out
    # cleanly is worth a lot -- it is the whole point of the exercise --
    # but not enough to justify dropping to a candidate we are much less
    # sure of, so alternatives have to stay within reach of the best score.
    floor = max(20.0, usable[0].score * 0.5)
    shortlist = [c for c in usable[:max_attempts] if c.score >= floor]

    attempts: list[dict[str, Any]] = []
    best: tuple[float, Candidate, Any] | None = None

    for candidate in shortlist:
        attempt: dict[str, Any] = {
            "file": candidate.filename,
            "score": candidate.score,
            "evidence": candidate.evidence,
        }
        try:
            cache_name = hashlib.sha1(candidate.filename.encode()).hexdigest()[:16]
            raw = commons.download(
                candidate.thumb_url, work_dir / "downloads" / f"{cache_name}.img"
            )
            with Image.open(io.BytesIO(raw)) as handle:
                handle.load()
                result = cut_out(handle)
        except (requests.RequestException, OSError, UnidentifiedImageError) as exc:
            attempt["outcome"] = f"failed: {exc}"
            attempts.append(attempt)
            continue

        # A cutout that fills its own bounding box means nothing was
        # removed: a full painted scene, or a plate inside a ruled frame.
        adjusted = candidate.score - (35 if result.suspect else 0)
        attempt["coverage"] = round(result.coverage, 3)
        attempt["dropped_blobs"] = result.dropped_blobs
        attempt["outcome"] = "nothing to remove" if result.suspect else "cut cleanly"
        attempts.append(attempt)

        if best is None or adjusted > best[0]:
            best = (adjusted, candidate, result)
        if not result.suspect:
            # Candidates are in descending score order, so nothing further
            # down the list can beat a clean cut at this position.
            break

    report["attempts"] = attempts
    if best is None:
        report["outcome"] = "every candidate failed to download"
        return None, report

    _, candidate, result = best
    blob, width, height = encode(result.image, longest_edge, quality)
    report["outcome"] = "ok"
    report["chosen"] = candidate.filename
    report["suspect"] = result.suspect
    report["bytes"] = len(blob)

    signed = any("signed" in e for e in candidate.evidence)
    return (
        PlateRecord(
            species_code=row["speciesCode"],
            rank=entry.rank,
            common_name=entry.common_name,
            scientific_name=row["sciName"],
            family=row.get("familyComName", ""),
            artist=ARTIST,
            attribution="signed on the plate" if signed else "attributed",
            work=candidate.work or candidate.filename,
            year=candidate.date,
            licence=LICENCE,
            source_url=candidate.description_url,
            commons_file=candidate.filename,
            image=blob,
            image_format="webp",
            width=width,
            height=height,
            coverage=result.coverage,
        ),
        report,
    )


# --------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE plates (
    species_code    TEXT PRIMARY KEY,
    rank            INTEGER NOT NULL,
    common_name     TEXT NOT NULL,
    scientific_name TEXT NOT NULL,
    family          TEXT NOT NULL DEFAULT '',
    artist          TEXT NOT NULL,
    attribution     TEXT NOT NULL,
    work            TEXT NOT NULL DEFAULT '',
    year            TEXT NOT NULL DEFAULT '',
    licence         TEXT NOT NULL,
    source_url      TEXT NOT NULL DEFAULT '',
    commons_file    TEXT NOT NULL DEFAULT '',
    image_format    TEXT NOT NULL,
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    sha256          TEXT NOT NULL,
    image           BLOB NOT NULL
);
CREATE INDEX plates_scientific ON plates (scientific_name);
CREATE INDEX plates_common     ON plates (common_name);
CREATE INDEX plates_rank       ON plates (rank);
"""


def write_db(path: Path, records: Sequence[PlateRecord]) -> None:
    """Write the database to a temp file, then move it into place.

    The Pi may be reading the old one; a half-written SQLite file in its
    place would be a broken board at 07:00.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.unlink(missing_ok=True)

    connection = sqlite3.connect(tmp)
    try:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO plates (species_code, rank, common_name, scientific_name,"
            " family, artist, attribution, work, year, licence, source_url,"
            " commons_file, image_format, width, height, sha256, image)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r.species_code,
                    r.rank,
                    r.common_name,
                    r.scientific_name,
                    r.family,
                    r.artist,
                    r.attribution,
                    r.work,
                    r.year,
                    r.licence,
                    r.source_url,
                    r.commons_file,
                    r.image_format,
                    r.width,
                    r.height,
                    hashlib.sha256(r.image).hexdigest(),
                    r.image,
                )
                for r in records
            ],
        )
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")),
                ("plate_count", str(len(records))),
                ("artist", ARTIST),
                ("licence", LICENCE),
                ("source", "Wikimedia Commons"),
            ],
        )
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def load_species(path: Path) -> list[SpeciesEntry]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["species"] if isinstance(data, dict) else data
    return [
        SpeciesEntry(
            rank=int(row["rank"]),
            common_name=row["common_name"],
            scientific_name=row["scientific_name"],
            synonyms=tuple(row.get("synonyms", [])),
        )
        for row in rows
    ]


# ------------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--species", type=Path, default=DEFAULT_SPECIES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--report", type=Path, help="write a per-species JSON report")
    parser.add_argument("--limit", type=int, help="only the first N species")
    parser.add_argument("--only", action="append", help="one species (any name); repeatable")
    parser.add_argument("--longest-edge", type=int, default=900)
    parser.add_argument("--quality", type=int, default=82)
    parser.add_argument("--config", help="path to config.toml (for the user agent)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    try:
        config = load_config(args.config)
        user_agent = config.http.user_agent
        request_width = config.image.request_width
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    entries = load_species(args.species)
    if args.only:
        wanted = {name.lower() for name in args.only}
        entries = [
            e
            for e in entries
            if e.common_name.lower() in wanted or e.scientific_name.lower() in wanted
        ]
    if args.limit:
        entries = entries[: args.limit]
    if not entries:
        log.error("no species selected")
        return 2

    args.work_dir.mkdir(parents=True, exist_ok=True)
    taxonomy = load_taxonomy(args.work_dir)
    commons = CommonsClient(user_agent, args.work_dir)

    records: list[PlateRecord] = []
    reports: list[dict[str, Any]] = []
    started = time.monotonic()

    for index, entry in enumerate(entries, start=1):
        record, report = build_species(
            commons,
            entry,
            taxonomy,
            args.work_dir,
            args.longest_edge,
            args.quality,
            request_width,
        )
        reports.append(report)
        if record is not None:
            records.append(record)
            log.info(
                "[%3d/%d] %-26s %-34s %5.1f kB  %s",
                index,
                len(entries),
                entry.common_name,
                record.commons_file[:34],
                len(record.image) / 1024,
                record.attribution,
            )
        else:
            log.warning(
                "[%3d/%d] %-26s no plate: %s",
                index,
                len(entries),
                entry.common_name,
                report.get("outcome"),
            )

    if records:
        write_db(args.out, records)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(reports, indent=1, ensure_ascii=False), encoding="utf-8"
        )

    total = sum(len(r.image) for r in records)
    log.info(
        "%d/%d species have a plate; %.1f MB in %s (%.0f s)",
        len(records),
        len(entries),
        total / 1e6,
        args.out,
        time.monotonic() - started,
    )
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
