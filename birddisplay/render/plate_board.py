"""The quiet board: one engraving, one name, and a line of hairline rules.

A second layout, for when the headline bird has a Keulemans plate rather
than a photograph. The photo board fills its left half edge to edge, which
is right for a photograph -- a photograph cropped small looks like a
thumbnail. A cut-out engraving wants the opposite treatment: the bird sits
in white space with nothing round it, the way it sits on the page of the
book it came from.

    ┌──────────────────────────────────────────────┐
    │ 12 AUGUST 2026                 BOWLING GREEN │   date left,
    │ ──────────────────────────────────────────── │   place right
    │                                              │
    │   Blackbird                    ╭────────╮    │
    │   Turdus merula                │  bird  │    │
    │   ───                          ╰────────╯    │
    │   07:14 · 3 birds                            │
    │ ──────────────────────────────────────────── │
    │ ALSO SEEN                                    │
    │ Whimbrel · Greenshank · Little Egret …       │   Times, bottom
    └──────────────────────────────────────────────┘

Everything is set in one serif family so the board reads as a printed page
rather than a dashboard, and the only colour on it comes from the bird
itself. Two rules earn their place: the plate is pasted through its own
alpha mask, so the white around the bird stays flat white ink instead of
dither noise, and the small type is never dithered at all.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

from PIL import Image, ImageDraw

from ..config import Config
from ..model import Board, Sighting
from . import fonts as F
from .palette import Ink, new_canvas, quantize_photo

log = logging.getLogger(__name__)

MARGIN = 44
HEADER_Y = 30
RULE_GAP = 18
FOOTER_LABEL = "ALSO SEEN"
SEPARATOR = " · "


def _tracked_width(text: str, font, tracking: float) -> float:
    if not text:
        return 0.0
    return sum(font.getlength(char) for char in text) + tracking * (len(text) - 1)


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font,
    ink: Ink,
    tracking: float = 2.4,
) -> None:
    """Draw letterspaced text, one character at a time.

    Pillow has no letter-spacing, and small caps without it look cramped.
    At this size the extra air is most of what makes the header read as a
    caption rather than as content.
    """
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=int(ink))
        x += font.getlength(char) + tracking


def _fit_contain(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale to fit inside a box, never cropping.

    A cut-out bird has already been cropped to its own outline; cropping it
    again to fill a rectangle would take its tail off.
    """
    scale = min(width / image.width, height / image.height)
    if scale >= 1:
        return image
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


class PlateBoardRenderer:
    """Renders a Board whose headline image is a cut-out plate."""

    def __init__(self, config: Config):
        self.config = config
        self.width = config.display.width
        self.height = config.display.height

        self.header = F.load_font("times", 15)
        self.name = F.load_font("times", 60)
        self.scientific = F.load_font("times_italic", 21)
        self.detail = F.load_font("times", 17)
        self.label = F.load_font("times", 12)
        self.listing = F.load_font("times", 17)
        self.credit = F.load_font("times", 11)

    # -- public ----------------------------------------------------------

    def _local(self, now: datetime | None) -> datetime:
        """The wall clock where the display hangs.

        The renderer used to run on the same Pi as the panel, so the system
        clock was already right. It now also runs on a GitHub runner set to
        UTC, while eBird reports British local time -- printing one over the
        other is how a board ends up dated yesterday. An aware time handed
        in is converted rather than trusted; a naive one is left alone,
        because the only thing that passes naive times is a machine whose
        clock is already local.
        """
        zone = self.config.region.tzinfo
        if now is None:
            return datetime.now(zone or timezone.utc)
        if now.tzinfo is not None and zone is not None:
            return now.astimezone(zone)
        return now

    def render(
        self,
        board: Board,
        plate: Image.Image | None,
        now: datetime | None = None,
    ) -> Image.Image:
        now = self._local(now)
        canvas = new_canvas(self.width, self.height, self.config.palette)
        draw = ImageDraw.Draw(canvas)

        header_bottom = self._draw_header(draw, board, now)
        footer_top = self._draw_footer(draw, board)

        if plate is not None:
            self._draw_plate(canvas, plate, header_bottom, footer_top)
        self._draw_subject(draw, board, header_bottom)
        return canvas

    # -- pieces ----------------------------------------------------------

    def _draw_header(
        self, draw: ImageDraw.ImageDraw, board: Board, now: datetime
    ) -> int:
        """Date top left, place top right, hairline under both."""
        date_text = now.strftime("%-d %B %Y").upper()
        _draw_tracked(draw, (MARGIN, HEADER_Y), date_text, self.header, Ink.BLACK)

        place = board.headline.location if board.headline else board.region_name
        place = self._shorten_to(place.upper(), self.header, 330, tracking=2.4)
        width = _tracked_width(place, self.header, 2.4)
        _draw_tracked(
            draw,
            (self.width - MARGIN - width, HEADER_Y),
            place,
            self.header,
            Ink.BLACK,
        )

        rule_y = HEADER_Y + F.line_height(self.header) + 6
        draw.line((MARGIN, rule_y, self.width - MARGIN, rule_y), fill=int(Ink.BLACK))

        if board.is_stale(self.config.cache.max_age_hours):
            # The one thing on this board that is allowed to shout.
            stale_y = rule_y + 6
            draw.text(
                (MARGIN, stale_y), "DATA STALE", font=self.label, fill=int(Ink.RED)
            )
        return rule_y

    def _draw_subject(
        self, draw: ImageDraw.ImageDraw, board: Board, header_bottom: int
    ) -> None:
        """The species, large, in the left third."""
        if board.headline is None:
            return
        species = board.headline.species
        column = 360

        font, name = F.fit_single_line(
            species.common_name, "times", column, (60, 52, 44, 38, 32)
        )
        y = header_bottom + 74
        draw.text((MARGIN, y), name, font=font, fill=int(Ink.BLACK))
        y += F.line_height(font) - 6

        if species.scientific_name:
            draw.text(
                (MARGIN, y),
                F.truncate(species.scientific_name, self.scientific, column),
                font=self.scientific,
                fill=int(Ink.BLACK),
            )
            y += F.line_height(self.scientific) + 22

        draw.line((MARGIN, y, MARGIN + 46, y), fill=int(Ink.BLACK))
        y += 20

        detail = self._detail_line(board.headline)
        if detail:
            draw.text(
                (MARGIN, y),
                F.truncate(detail, self.detail, column),
                font=self.detail,
                fill=int(Ink.BLACK),
            )

    def _draw_plate(
        self,
        canvas: Image.Image,
        plate: Image.Image,
        header_bottom: int,
        footer_top: int,
    ) -> None:
        """Paste the bird through its own alpha, so the ground stays white.

        Quantising the whole rectangle and pasting it would dither the
        paper as well, and a faint checkerboard of stray ink around the
        bird is exactly what makes a six-colour panel look cheap.
        """
        box_left = 400
        box_top = header_bottom + 16
        box_width = self.width - MARGIN - box_left
        box_height = footer_top - box_top - 16
        if box_width <= 0 or box_height <= 0:
            return

        image = plate if plate.mode == "RGBA" else plate.convert("RGBA")
        image = _fit_contain(image, box_width, box_height)

        # Dither against white, which is what it will actually sit on.
        flattened = Image.new("RGB", image.size, (255, 255, 255))
        flattened.paste(image, mask=image.split()[3])
        dithered = quantize_photo(
            flattened, self.config.plates.enhancement, self.config.palette
        )

        mask = image.split()[3].point(lambda a: 255 if a > 128 else 0)
        x = box_left + (box_width - image.width) // 2
        y = box_top + (box_height - image.height) // 2
        canvas.paste(dithered, (x, y), mask)

    def _draw_footer(self, draw: ImageDraw.ImageDraw, board: Board) -> int:
        """Label, then the other sightings as one flowing line of names.

        A table of names, times and places would carry more information and
        be worth less: this is read from across a room, and what it has to
        say is "these birds are about".
        """
        credit_height = F.line_height(self.credit)
        bottom = self.height - MARGIN + 6
        listing_height = F.line_height(self.listing)

        names = self._also_seen_names(board)
        lines = (
            F.wrap(SEPARATOR.join(names), self.listing, self.width - 2 * MARGIN, 2)
            if names
            else []
        )

        block_height = len(lines) * listing_height
        label_height = F.line_height(self.label)
        top = bottom - credit_height - block_height - label_height - RULE_GAP

        rule_y = top - RULE_GAP
        draw.line((MARGIN, rule_y, self.width - MARGIN, rule_y), fill=int(Ink.BLACK))

        y = top
        _draw_tracked(draw, (MARGIN, y), FOOTER_LABEL, self.label, Ink.BLACK, 2.8)
        y += label_height + 4

        for line in lines:
            draw.text((MARGIN, y), line, font=self.listing, fill=int(Ink.BLACK))
            y += listing_height

        credit = board.headline_photo.credit_line if board.headline_photo else ""
        if credit:
            width = self.credit.getlength(credit)
            draw.text(
                (self.width - MARGIN - width, self.height - MARGIN + 8),
                credit,
                font=self.credit,
                fill=int(Ink.BLACK),
            )
        return rule_y

    # -- text ------------------------------------------------------------

    def _detail_line(self, sighting: Sighting) -> str:
        bits: list[str] = []
        if sighting.observed_at:
            bits.append(sighting.observed_at.strftime("%H:%M"))
        if sighting.count and sighting.count > 1:
            bits.append(f"{sighting.count} birds")
        if sighting.notable:
            bits.append("notable")
        return SEPARATOR.join(bits)

    def _also_seen_names(self, board: Board) -> list[str]:
        seen: set[str] = set()
        names: list[str] = []
        headline = board.headline.species.code if board.headline else None
        for sighting in board.also_seen:
            code = sighting.species.code
            if code == headline or code in seen:
                continue
            seen.add(code)
            names.append(sighting.species.common_name)
        return names[: self.config.board.also_seen_count]

    def _shorten_to(self, text: str, font, limit: float, tracking: float) -> str:
        while text and _tracked_width(text, font, tracking) > limit:
            text = text[:-1]
        return text.rstrip(" ,")


def render_board(
    config: Config,
    board: Board,
    plate: Image.Image | None,
    now: datetime | None = None,
) -> Image.Image:
    return PlateBoardRenderer(config).render(board, plate, now)


def sightings_from(board: Board) -> Sequence[Sighting]:
    return board.also_seen
