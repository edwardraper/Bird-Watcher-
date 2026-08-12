"""The quiet board: one bird, one name, and a line of hairline rules.

The layout for a headline that has a Keulemans plate rather than a
photograph -- and, when the frame hangs on its side, for photographs too.

Two compositions, one type scale:

    landscape 800x480              portrait 480x800
    ┌──────────────────────┐       ┌──────────────┐
    │ 12 AUG      THORPE   │       │ 12 AUG THORPE│  date left,
    │ ──────────────────── │       │ ──────────── │  place right
    │                      │       │              │
    │  Blackbird   ╭─────╮ │       │    ╭──────╮  │
    │  Turdus      │bird │ │       │    │ bird │  │
    │  ───         ╰─────╯ │       │    ╰──────╯  │
    │  07:14               │       │     ───      │
    │ ──────────────────── │       │   Blackbird  │
    │ ALSO SEEN            │       │ Turdus merula│
    │ Whimbrel · Greenshank│       │    07:14     │
    └──────────────────────┘       │ ──────────── │
                                   │ ALSO SEEN    │
                                   │ Whimbrel · … │
                                   └──────────────┘

Portrait is centred and landscape is not, for the same reason a printed
plate is centred on its page and a spread is not: with the bird above the
name there is a single axis to hang everything on, and using it is most of
what makes the board look printed rather than laid out.

Everything is set in one serif family, and the only colour comes from the
bird itself. Two rules earn their place: a cut-out plate is pasted through
its own alpha so the paper around it stays flat white ink rather than
dither noise, and the small type is never dithered at all.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from PIL import Image, ImageDraw

from ..config import Config
from ..model import Board, Sighting
from . import fonts as F
from .palette import Ink, fit_cover, new_canvas, quantize_photo

log = logging.getLogger(__name__)

FOOTER_LABEL = "ALSO SEEN"
SEPARATOR = " · "
HEADER_Y = 30


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
    """Renders a Board around one image, in either orientation."""

    def __init__(self, config: Config):
        self.config = config
        self.width, self.height = config.display.board_size
        self.portrait = config.display.portrait

        self.margin = 36 if self.portrait else 44
        self.rule_gap = 18
        self.header = F.load_font("times", 14 if self.portrait else 15)
        self.scientific = F.load_font("times_italic", 22 if self.portrait else 21)
        self.detail = F.load_font("times", 17)
        self.label = F.load_font("times", 12)
        self.listing = F.load_font("times", 16 if self.portrait else 17)
        self.credit = F.load_font("times", 11)
        self.name_sizes = (56, 48, 42, 36, 30) if self.portrait else (60, 52, 44, 38, 32)

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
        image: Image.Image | None,
        now: datetime | None = None,
    ) -> Image.Image:
        now = self._local(now)
        canvas = new_canvas(self.width, self.height, self.config.palette)
        draw = ImageDraw.Draw(canvas)

        header_bottom = self._draw_header(draw, board, now)
        footer_top = self._draw_footer(draw, board)

        if self.portrait:
            text_top = self._portrait_text_top(board, footer_top)
            if image is not None:
                self._draw_image(canvas, image, header_bottom + 26, text_top - 24)
            self._draw_subject_centred(draw, board, text_top)
        else:
            if image is not None:
                self._draw_image(
                    canvas, image, header_bottom + 16, footer_top - 16, left=400
                )
            self._draw_subject_left(draw, board, header_bottom)
        return canvas

    # -- header and footer -------------------------------------------------

    def _draw_header(
        self, draw: ImageDraw.ImageDraw, board: Board, now: datetime
    ) -> int:
        """Date top left, place top right, hairline under both."""
        tracking = 1.8 if self.portrait else 2.4
        date_text = now.strftime("%-d %b %Y" if self.portrait else "%-d %B %Y").upper()
        _draw_tracked(
            draw, (self.margin, HEADER_Y), date_text, self.header, Ink.BLACK, tracking
        )

        place = board.headline.location if board.headline else board.region_name
        room = (
            self.width
            - 2 * self.margin
            - _tracked_width(date_text, self.header, tracking)
            - 24
        )
        place = self._shorten_to(place.upper(), self.header, room, tracking)
        width = _tracked_width(place, self.header, tracking)
        _draw_tracked(
            draw,
            (self.width - self.margin - width, HEADER_Y),
            place,
            self.header,
            Ink.BLACK,
            tracking,
        )

        rule_y = HEADER_Y + F.line_height(self.header) + 6
        draw.line(
            (self.margin, rule_y, self.width - self.margin, rule_y), fill=int(Ink.BLACK)
        )

        if board.is_stale(self.config.cache.max_age_hours):
            # The one thing on this board that is allowed to shout.
            draw.text(
                (self.margin, rule_y + 6),
                "DATA STALE",
                font=self.label,
                fill=int(Ink.RED),
            )
        return rule_y

    def _draw_footer(self, draw: ImageDraw.ImageDraw, board: Board) -> int:
        """Label, then the other sightings as one flowing line of names.

        A table of names, times and places would carry more information and
        be worth less: this is read from across a room, and what it has to
        say is "these birds are about".
        """
        credit_height = F.line_height(self.credit)
        bottom = self.height - self.margin + 6
        listing_height = F.line_height(self.listing)

        names = self._also_seen_names(board)
        lines = (
            F.wrap(
                SEPARATOR.join(names),
                self.listing,
                self.width - 2 * self.margin,
                3 if self.portrait else 2,
            )
            if names
            else []
        )

        label_height = F.line_height(self.label)
        top = (
            bottom
            - credit_height
            - len(lines) * listing_height
            - label_height
            - self.rule_gap
        )

        rule_y = top - self.rule_gap
        draw.line(
            (self.margin, rule_y, self.width - self.margin, rule_y), fill=int(Ink.BLACK)
        )

        y = top
        _draw_tracked(draw, (self.margin, y), FOOTER_LABEL, self.label, Ink.BLACK, 2.8)
        y += label_height + 4
        for line in lines:
            draw.text((self.margin, y), line, font=self.listing, fill=int(Ink.BLACK))
            y += listing_height

        credit = board.headline_photo.credit_line if board.headline_photo else ""
        if credit:
            text = F.truncate(credit, self.credit, self.width - 2 * self.margin)
            width = self.credit.getlength(text)
            draw.text(
                (self.width - self.margin - width, self.height - self.margin + 8),
                text,
                font=self.credit,
                fill=int(Ink.BLACK),
            )
        return rule_y

    # -- the subject -------------------------------------------------------

    def _name_font(self, board: Board, column: float):
        name = board.headline.species.common_name if board.headline else ""
        return F.fit_single_line(name, "times", column, self.name_sizes)

    def _portrait_text_top(self, board: Board, footer_top: int) -> int:
        """Where the name block starts, so the picture knows where to stop.

        Measured rather than guessed: a short rule, the name, the
        scientific name and one line of detail, sitting above the footer
        rule with air around it.
        """
        column = self.width - 2 * self.margin
        font, _ = self._name_font(board, column)
        height = 18 + F.line_height(font) + F.line_height(self.scientific) + 10
        if board.headline and self._detail_line(board.headline):
            height += F.line_height(self.detail) + 10
        return int(footer_top - self.rule_gap - height)

    def _draw_subject_centred(
        self, draw: ImageDraw.ImageDraw, board: Board, top: int
    ) -> None:
        """Portrait: a short rule, then the names on the centre line."""
        if board.headline is None:
            return
        species = board.headline.species
        column = self.width - 2 * self.margin
        centre = self.width // 2

        draw.line((centre - 22, top, centre + 22, top), fill=int(Ink.BLACK))
        y = top + 18

        font, name = self._name_font(board, column)
        draw.text((centre, y), name, font=font, fill=int(Ink.BLACK), anchor="ma")
        y += F.line_height(font)

        if species.scientific_name:
            draw.text(
                (centre, y),
                F.truncate(species.scientific_name, self.scientific, column),
                font=self.scientific,
                fill=int(Ink.BLACK),
                anchor="ma",
            )
            y += F.line_height(self.scientific) + 10

        detail = self._detail_line(board.headline)
        if detail:
            draw.text(
                (centre, y),
                F.truncate(detail, self.detail, column),
                font=self.detail,
                fill=int(Ink.BLACK),
                anchor="ma",
            )

    def _draw_subject_left(
        self, draw: ImageDraw.ImageDraw, board: Board, header_bottom: int
    ) -> None:
        """Landscape: the species, large, in the left third."""
        if board.headline is None:
            return
        species = board.headline.species
        column = 360

        font, name = self._name_font(board, column)
        y = header_bottom + 74
        draw.text((self.margin, y), name, font=font, fill=int(Ink.BLACK))
        y += F.line_height(font) - 6

        if species.scientific_name:
            draw.text(
                (self.margin, y),
                F.truncate(species.scientific_name, self.scientific, column),
                font=self.scientific,
                fill=int(Ink.BLACK),
            )
            y += F.line_height(self.scientific) + 22

        draw.line((self.margin, y, self.margin + 46, y), fill=int(Ink.BLACK))
        y += 20

        detail = self._detail_line(board.headline)
        if detail:
            draw.text(
                (self.margin, y),
                F.truncate(detail, self.detail, column),
                font=self.detail,
                fill=int(Ink.BLACK),
            )

    # -- the picture -------------------------------------------------------

    def _draw_image(
        self,
        canvas: Image.Image,
        image: Image.Image,
        top: int,
        bottom: int,
        left: int | None = None,
    ) -> None:
        """Put the bird in the space between the rules.

        A cut-out plate is fitted whole and pasted through its own alpha,
        so the paper around it stays flat white ink -- quantising the whole
        rectangle would dither the background too, and a faint checkerboard
        of stray ink around the bird is what makes a six-colour panel look
        cheap. A photograph has no such edge, so it is cropped to fill.
        """
        box_left = self.margin if left is None else left
        box_width = self.width - self.margin - box_left
        box_height = bottom - top
        if box_width <= 0 or box_height <= 0:
            return

        if "A" in image.getbands():
            plate = image.convert("RGBA")
            plate = _fit_contain(plate, box_width, box_height)
            flattened = Image.new("RGB", plate.size, (255, 255, 255))
            flattened.paste(plate, mask=plate.split()[3])
            dithered = quantize_photo(
                flattened, self.config.plates.enhancement, self.config.palette
            )
            mask = plate.split()[3].point(lambda a: 255 if a > 128 else 0)
            x = box_left + (box_width - plate.width) // 2
            y = top + (box_height - plate.height) // 2
            canvas.paste(dithered, (x, y), mask)
            return

        photo = fit_cover(image.convert("RGB"), box_width, box_height)
        dithered = quantize_photo(photo, self.config.image, self.config.palette)
        canvas.paste(dithered, (box_left, top))

    # -- text --------------------------------------------------------------

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
        return text.rstrip(" ,-")


def render_board(
    config: Config,
    board: Board,
    image: Image.Image | None,
    now: datetime | None = None,
) -> Image.Image:
    return PlateBoardRenderer(config).render(board, image, now)
