"""Configuration loading and validation.

The whole app is driven by one TOML file plus one environment variable
(the eBird API key). Nothing else reaches into os.environ.
"""

from __future__ import annotations

import os
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


class ConfigError(Exception):
    """Raised when the config file is missing, malformed or out of range."""


@dataclass(frozen=True)
class RegionConfig:
    code: str
    name: str
    lat: float
    lng: float
    radius_km: int
    back_days: int
    notable_back_days: int
    # Ask for rare birds by radius rather than by county. The two eBird
    # queries behind the board answer different questions: "seen nearby"
    # is a circle around lat/lng, while the rare-bird feed is a whole
    # administrative region -- and Derbyshire is about ninety kilometres
    # top to bottom, so a county query can headline the board with a bird
    # seen sixty kilometres away. True asks the geo endpoint instead, so
    # both halves of the board mean the same thing by "near".
    notable_within_radius: bool = True
    timezone: str = "Europe/London"

    @property
    def tzinfo(self) -> ZoneInfo | None:
        """Where the display hangs, for anything it prints.

        The renderer used to run on the same Pi as the panel, where the
        system clock was already local. It now runs on a GitHub runner set
        to UTC, while eBird reports sightings in British local time -- so
        without this the board would print a UTC date over local times,
        and for an hour around midnight in summer, the wrong date.
        """
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            # A missing tz database is not worth a blank wall; fall back
            # to the system clock, which on the Pi is right anyway.
            return None


@dataclass(frozen=True)
class EbirdConfig:
    base_url: str
    locale: str
    api_key_env: str
    taxonomy_max_age_days: int

    @property
    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise ConfigError(
                f"No eBird API key in ${self.api_key_env}. "
                "Get one at https://ebird.org/api/keygen and export it."
            )
        return key


@dataclass(frozen=True)
class HttpConfig:
    user_agent: str
    timeout_seconds: float
    retries: int
    backoff_seconds: float


@dataclass(frozen=True)
class CacheConfig:
    dir: Path
    max_age_hours: float

    @property
    def board_path(self) -> Path:
        return self.dir / "cache.json"

    @property
    def taxonomy_path(self) -> Path:
        return self.dir / "taxonomy.json"

    @property
    def state_path(self) -> Path:
        """Cross-run state: which species we have featured recently."""
        return self.dir / "state.json"

    @property
    def image_dir(self) -> Path:
        return self.dir / "images"


@dataclass(frozen=True)
class BoardConfig:
    also_seen_count: int
    feature_memory: int
    prefer_notable: bool


@dataclass(frozen=True)
class PlatesConfig:
    """The Keulemans plate database, if there is one.

    Optional in every direction: the section may be absent, the file may
    be missing, and either way the board falls back to photographs.
    """

    enabled: bool
    path: Path
    prefer_plates: bool
    saturation: float = 1.05
    contrast: float = 1.25
    sharpness: float = 1.20

    @property
    def enhancement(self) -> "ImageConfig":
        """Pre-dither treatment for a plate, which is not a photograph.

        [image] pushes saturation hard because photographic mid-tones
        smear when they are reduced to six inks. Doing that to a pale
        lithograph turns the paper into orange confetti; a drawing has
        flat colour already and wants contrast instead, to hold the bird
        apart from the ground. request_width is unused here -- plates come
        off the disk at the size they were built.
        """
        return ImageConfig(
            saturation=self.saturation,
            contrast=self.contrast,
            sharpness=self.sharpness,
            request_width=0,
        )


@dataclass(frozen=True)
class ImageConfig:
    saturation: float
    contrast: float
    sharpness: float
    request_width: int


@dataclass(frozen=True)
class DisplayConfig:
    width: int
    height: int
    backend: str
    preview_path: Path
    rotate: int
    # Where the "inky" backend writes the board for the frame to fetch.
    # Its .json sidecar sits beside it.
    export_path: Path = Path("board.png")

    @property
    def portrait(self) -> bool:
        return self.rotate in (90, 270)

    @property
    def board_size(self) -> tuple[int, int]:
        """The canvas the renderer composes on.

        width/height describe the panel, which is landscape and always
        will be -- the glass does not turn. Hanging the frame on its side
        is a rotation applied on the way out, so everything upstream of
        the display backend draws 480x800 and never thinks about it again.
        """
        if self.portrait:
            return self.height, self.width
        return self.width, self.height


# Nominal primaries, used to decide which ink each pixel becomes. These
# are deliberately saturated: matching against the muted values the panel
# really prints drags every mid-tone onto a coloured ink, because a muted
# blue sits closer to mid-grey than black or white does. Saturated match
# colours keep neutrals neutral and spend ink only where there is colour.
NOMINAL_MATCH_RGB: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (255, 255, 255),
    (255, 255, 0),
    (255, 0, 0),
    (0, 0, 255),
    (0, 255, 0),
)


@dataclass(frozen=True)
class PaletteConfig:
    """RGB values for the six Spectra 6 inks, in panel index order.

    Two tables, doing two different jobs:

    * the six named fields are how the inks *look* -- used only to render
      preview PNGs, since the panel receives ink indices and nothing else;
    * `match` is what pixels are compared against when quantising.
    """

    black: tuple[int, int, int]
    white: tuple[int, int, int]
    yellow: tuple[int, int, int]
    red: tuple[int, int, int]
    blue: tuple[int, int, int]
    green: tuple[int, int, int]
    match: tuple[tuple[int, int, int], ...] = NOMINAL_MATCH_RGB

    def as_list(self) -> list[tuple[int, int, int]]:
        return [self.black, self.white, self.yellow, self.red, self.blue, self.green]

    def match_list(self) -> list[tuple[int, int, int]]:
        return list(self.match)


@dataclass(frozen=True)
class Config:
    region: RegionConfig
    ebird: EbirdConfig
    http: HttpConfig
    cache: CacheConfig
    board: BoardConfig
    plates: PlatesConfig
    image: ImageConfig
    display: DisplayConfig
    palette: PaletteConfig
    source_path: Path = field(default=DEFAULT_CONFIG_PATH)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"config is missing the [{name}] section")
    return section


def _rgb(section: dict[str, Any], key: str) -> tuple[int, int, int]:
    value = section.get(key)
    if not isinstance(value, list) or len(value) != 3:
        raise ConfigError(f"[palette].{key} must be a list of three integers")
    out = []
    for channel in value:
        if not isinstance(channel, int) or not 0 <= channel <= 255:
            raise ConfigError(f"[palette].{key} channels must be integers 0-255")
        out.append(channel)
    return (out[0], out[1], out[2])


def load_config(path: Path | str | None = None) -> Config:
    """Read and validate the TOML config."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    with config_path.open("rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{config_path} is not valid TOML: {exc}") from exc

    region_raw = _section(data, "region")
    region = RegionConfig(
        code=str(region_raw["code"]),
        name=str(region_raw.get("name", region_raw["code"])),
        lat=float(region_raw["lat"]),
        lng=float(region_raw["lng"]),
        radius_km=int(region_raw.get("radius_km", 25)),
        back_days=int(region_raw.get("back_days", 5)),
        notable_back_days=int(region_raw.get("notable_back_days", 7)),
        notable_within_radius=bool(region_raw.get("notable_within_radius", True)),
        timezone=str(region_raw.get("timezone", "Europe/London")),
    )
    # eBird enforces these server-side; failing here gives a better message
    # than a 400 from the API at 06:50 on a Sunday.
    if not -90 <= region.lat <= 90 or not -180 <= region.lng <= 180:
        raise ConfigError("[region].lat/lng are out of range")
    if not 1 <= region.radius_km <= 50:
        raise ConfigError("[region].radius_km must be 1-50 (eBird caps dist at 50km)")
    for key in ("back_days", "notable_back_days"):
        days = getattr(region, key)
        if not 1 <= days <= 30:
            raise ConfigError(f"[region].{key} must be 1-30 (eBird caps back at 30)")

    ebird_raw = _section(data, "ebird")
    ebird = EbirdConfig(
        base_url=str(ebird_raw.get("base_url", "https://api.ebird.org/v2")).rstrip("/"),
        locale=str(ebird_raw.get("locale", "en_UK")),
        api_key_env=str(ebird_raw.get("api_key_env", "EBIRD_API_KEY")),
        taxonomy_max_age_days=int(ebird_raw.get("taxonomy_max_age_days", 30)),
    )

    http_raw = _section(data, "http")
    http = HttpConfig(
        user_agent=str(http_raw["user_agent"]),
        timeout_seconds=float(http_raw.get("timeout_seconds", 10.0)),
        retries=int(http_raw.get("retries", 3)),
        backoff_seconds=float(http_raw.get("backoff_seconds", 1.0)),
    )
    if not http.user_agent.strip():
        raise ConfigError(
            "[http].user_agent must be set -- Wikimedia's policy requires a "
            "descriptive UA with contact info"
        )

    cache_raw = _section(data, "cache")
    cache = CacheConfig(
        dir=Path(str(cache_raw.get("dir", "~/.cache/birddisplay"))).expanduser(),
        max_age_hours=float(cache_raw.get("max_age_hours", 48)),
    )

    board_raw = _section(data, "board")
    board = BoardConfig(
        also_seen_count=int(board_raw.get("also_seen_count", 8)),
        feature_memory=int(board_raw.get("feature_memory", 6)),
        prefer_notable=bool(board_raw.get("prefer_notable", True)),
    )

    # Optional section: a config written before the plate database existed
    # is still a valid config, and the defaults are what this repository
    # ships.
    plates_raw = data.get("plates") or {}
    plates_path = Path(str(plates_raw.get("path", "assets/plates/keulemans_uk.sqlite")))
    if not plates_path.is_absolute():
        plates_path = config_path.resolve().parent / plates_path
    plates = PlatesConfig(
        enabled=bool(plates_raw.get("enabled", True)),
        path=plates_path,
        prefer_plates=bool(plates_raw.get("prefer_plates", True)),
        saturation=float(plates_raw.get("saturation", 1.05)),
        contrast=float(plates_raw.get("contrast", 1.25)),
        sharpness=float(plates_raw.get("sharpness", 1.20)),
    )

    image_raw = _section(data, "image")
    image = ImageConfig(
        saturation=float(image_raw.get("saturation", 1.3)),
        contrast=float(image_raw.get("contrast", 1.2)),
        sharpness=float(image_raw.get("sharpness", 1.4)),
        request_width=int(image_raw.get("request_width", 1200)),
    )

    display_raw = _section(data, "display")
    display = DisplayConfig(
        width=int(display_raw.get("width", 800)),
        height=int(display_raw.get("height", 480)),
        backend=str(display_raw.get("backend", "preview")).lower(),
        preview_path=Path(str(display_raw.get("preview_path", "preview.png"))).expanduser(),
        rotate=int(display_raw.get("rotate", 0)),
        export_path=Path(str(display_raw.get("export_path", "board.png"))).expanduser(),
    )
    if display.backend not in {"epaper", "preview", "inky"}:
        raise ConfigError(
            '[display].backend must be "epaper", "preview" or "inky"'
        )
    if display.rotate not in {0, 90, 180, 270}:
        raise ConfigError(
            "[display].rotate must be 0, 90, 180 or 270 -- 90 and 270 hang "
            "the frame on its side and draw a portrait board"
        )

    palette_raw = _section(data, "palette")
    match_raw = palette_raw.get("match")
    if isinstance(match_raw, dict):
        match = tuple(
            _rgb(match_raw, key)
            for key in ("black", "white", "yellow", "red", "blue", "green")
        )
    else:
        match = NOMINAL_MATCH_RGB
    palette = PaletteConfig(
        black=_rgb(palette_raw, "black"),
        white=_rgb(palette_raw, "white"),
        yellow=_rgb(palette_raw, "yellow"),
        red=_rgb(palette_raw, "red"),
        blue=_rgb(palette_raw, "blue"),
        green=_rgb(palette_raw, "green"),
        match=match,
    )

    return Config(
        region=region,
        ebird=ebird,
        http=http,
        cache=cache,
        board=board,
        plates=plates,
        image=image,
        display=display,
        palette=palette,
        source_path=config_path,
    )
