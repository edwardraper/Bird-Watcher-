"""Bird photography from Wikimedia, with attribution and a disk cache.

Not Macaulay Library: the licensing there is per-asset and mostly not ours
to use. Wikimedia has a usable image for essentially every British species,
with explicit licences and an API that tells us who to credit.

The credit line is a licence condition for most CC images. It costs one
line of 10px text, so we always render it.

Route: scientific name -> Wikipedia article -> lead image -> Commons
imageinfo for artist and licence -> JPEG on disk keyed by species code.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from ..cache import write_json_atomic
from ..config import Config
from ..model import Photo, Species
from .http import FetchError, HttpClient
from .plates import PlateStore

log = logging.getLogger(__name__)

WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_THUMB_PREFIX_RE = re.compile(r"^\d+px-")


class ImageUnavailable(Exception):
    """No usable image for this species. Callers fall back to text-only."""


def _clean_html(raw: str | None) -> str:
    """extmetadata fields arrive as HTML fragments -- flatten to plain text."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _shorten(text: str, limit: int = 60) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class _FileRef:
    """A Commons (or local enwiki) file we intend to download."""

    filename: str
    url: str


class ImageSource:
    def __init__(self, config: Config, http: HttpClient):
        self.config = config
        self.http = http
        self.cache_dir = config.cache.image_dir
        # Opened once per run and never re-checked: a plate database that
        # is missing or unreadable is not an error, it just means every
        # bird comes from Wikimedia as it did before plates existed.
        self.plates = (
            PlateStore.maybe(config.plates.path) if config.plates.enabled else None
        )
        if self.plates is not None:
            log.debug("plate database open: %d plates", self.plates.count())

    # -- disk cache ------------------------------------------------------

    def _image_path(self, species_code: str) -> Path:
        return self.cache_dir / f"{species_code}.jpg"

    def _meta_path(self, species_code: str) -> Path:
        return self.cache_dir / f"{species_code}.json"

    def cached(self, species_code: str) -> Photo | None:
        """Return the cached photo for a species, if we already have it.

        There is no reason to re-download a robin.
        """
        image_path = self._image_path(species_code)
        meta_path = self._meta_path(species_code)
        if not image_path.exists() or image_path.stat().st_size == 0:
            return None
        credit, licence, source = "", "", ""
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                credit = meta.get("credit", "")
                licence = meta.get("licence", "")
                source = meta.get("source_url", "")
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("bad image metadata for %s: %s", species_code, exc)
        return Photo(
            path=str(image_path), credit=credit, licence=licence, source_url=source
        )

    # -- Wikimedia -------------------------------------------------------

    def _summary(self, title: str) -> dict[str, Any] | None:
        url = WIKIPEDIA_SUMMARY.format(title=quote(title.replace(" ", "_"), safe=""))
        try:
            data = self.http.get_json(url)
        except FetchError as exc:
            log.info("no Wikipedia summary for %r: %s", title, exc)
            return None
        if not isinstance(data, dict):
            return None
        if data.get("type") == "disambiguation":
            log.info("%r is a disambiguation page; skipping", title)
            return None
        return data

    def _lead_image(self, species: Species) -> _FileRef | None:
        """Find the lead image of the species' Wikipedia article.

        Scientific name first: it is unambiguous and Wikipedia redirects
        it to the article. Common names are a fallback and can collide
        with non-bird meanings, which is what the disambiguation check
        above is for.
        """
        candidates = [
            name
            for name in (species.scientific_name, species.common_name)
            if name and name.strip()
        ]
        for title in candidates:
            summary = self._summary(title)
            if summary is None:
                continue
            source = (summary.get("originalimage") or {}).get("source") or (
                summary.get("thumbnail") or {}
            ).get("source")
            if not source:
                log.info("Wikipedia article %r has no lead image", title)
                continue
            # Thumbnail URLs look like .../commons/a/ab/File.jpg/640px-File.jpg,
            # so strip any size prefix to recover the real file name.
            filename = _THUMB_PREFIX_RE.sub("", unquote(source.rsplit("/", 1)[-1]))
            return _FileRef(filename=filename, url=source)
        return None

    def _file_info(self, filename: str) -> tuple[str, str, str, str]:
        """Look up a file's download URL, artist, licence and description page.

        Tries Commons first, then the local en.wikipedia file table -- a few
        images are uploaded locally rather than to Commons.
        """
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "titles": f"File:{filename}",
            "prop": "imageinfo",
            "iiprop": "extmetadata|url",
            "iiurlwidth": self.config.image.request_width,
        }
        for api in (COMMONS_API, WIKIPEDIA_API):
            try:
                data = self.http.get_json(api, params=params)
            except FetchError as exc:
                log.info("imageinfo lookup failed at %s: %s", api, exc)
                continue
            pages = (data.get("query") or {}).get("pages") or []
            for page in pages:
                if page.get("missing"):
                    continue
                info = (page.get("imageinfo") or [{}])[0]
                extmeta = info.get("extmetadata") or {}
                artist = _clean_html(extmeta.get("Artist", {}).get("value"))
                licence = _clean_html(
                    extmeta.get("LicenseShortName", {}).get("value")
                ) or _clean_html(extmeta.get("License", {}).get("value"))
                url = info.get("thumburl") or info.get("url") or ""
                return url, artist, licence, info.get("descriptionurl", "")
        return "", "", "", ""

    def _download(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        response = self.http.get(url, stream=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)
            if tmp.stat().st_size == 0:
                raise ImageUnavailable(f"{url} returned an empty body")
            tmp.replace(dest)
        finally:
            response.close()
            tmp.unlink(missing_ok=True)

    # -- public ----------------------------------------------------------

    def _plate(self, species: Species) -> Photo | None:
        """The Keulemans plate for this bird, if the database has one."""
        if self.plates is None:
            return None
        plate = self.plates.get(species)
        if plate is None:
            return None
        try:
            photo = plate.as_photo(self.cache_dir)
        except OSError as exc:
            log.warning("could not write the plate for %s: %s", species.code, exc)
            return None
        log.info("using the %s plate for %s", plate.work or "Keulemans", species.code)
        return photo

    def photo_for(self, species: Species, refresh: bool = False) -> Photo:
        """Get a local image plus attribution for a species.

        The plate database is consulted first: it is on disk, so it costs
        no network, cannot be rate-limited, and a lithograph survives six
        inks better than a photograph. Wikimedia covers everything else.

        Raises ImageUnavailable if there is nothing usable; the caller
        should try another species or fall back to a text-only board.
        """
        prefer_plates = self.config.plates.prefer_plates
        if prefer_plates:
            plate = self._plate(species)
            if plate is not None:
                return plate
        try:
            return self._wikimedia_photo(species, refresh)
        except (ImageUnavailable, FetchError):
            if not prefer_plates:
                plate = self._plate(species)
                if plate is not None:
                    return plate
            raise

    def _wikimedia_photo(self, species: Species, refresh: bool = False) -> Photo:
        if not refresh:
            cached = self.cached(species.code)
            if cached is not None:
                log.debug("using cached image for %s", species.code)
                return cached

        ref = self._lead_image(species)
        if ref is None:
            raise ImageUnavailable(f"no Wikipedia lead image for {species.common_name}")

        url, artist, licence, description_url = self._file_info(ref.filename)
        if not url:
            # Attribution lookup failed but we still have the article's own
            # image URL. Fall back to it and credit Wikimedia Commons.
            log.info("no imageinfo for %s; using the article image URL", ref.filename)
            url = ref.url

        dest = self._image_path(species.code)
        try:
            self._download(url, dest)
        except (FetchError, OSError) as exc:
            raise ImageUnavailable(f"could not download {url}: {exc}") from exc

        credit = _shorten(artist) if artist else "Wikimedia Commons"
        photo = Photo(
            path=str(dest),
            credit=credit,
            licence=_shorten(licence, 28),
            source_url=description_url or ref.url,
        )
        write_json_atomic(
            self._meta_path(species.code),
            {
                "credit": photo.credit,
                "licence": photo.licence,
                "source_url": photo.source_url,
                "filename": ref.filename,
            },
        )
        log.info("cached image for %s (%s)", species.code, photo.credit_line)
        return photo
