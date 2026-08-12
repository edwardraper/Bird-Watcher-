"""Composition of the 800x480 board.

A printed page, not a dashboard. The panel refreshes two or three times a
day, so this has to hold up for six hours at a glance from across a room:
one big photograph, one species named large, and a quiet digest underneath.

Everything is drawn into a palettised canvas, which means text lands in
exact ink colours and is never dithered.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, UnidentifiedImageError

from ..config import Config
from ..model import Board, Sighting
from . import fonts as F
from .palette import Ink, new_canvas, prepare_photo

log = logging.getLogger(__name__)

PHOTO_SIZE = 480
PAD = 20
GAP = 10


def _fmt_when(when: datetime | None, now: datetime) -> str:
    """A human phrase for an observation time, not a timestamp."""
    if when is None:
        return ""
    days = (now.date() - when.date()).days
    time_part = "" if (when.hour == 0 and when.minute == 0) else when.strftime("%H:%M")
    if days <= 0:
        label = "Today"
    elif days == 1:
        label = "Yesterday"
    elif days < 7:
        label = when.strftime("%A")
    else:
        label = when.strftime("%-d %b")
    return f"{label} {time_part}".strip()


def _fmt_day(when: datetime | None, now: datetime) -> str:
    """The short right-hand column in the digest list."""
    if when is None:
        return ""
    days = (now.date() - when.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yest."
    if days < 7:
        return when.strftime("%a").lower()
    return when.strftime("%-d %b")


def _fmt_count(sighting: Sighting) -> str:
    if sighting.count and sighting.count > 1:
        return f"{sighting.count}"
    return ""


def _load_photo(path: str | None) -> Image.Image | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        log.warning("cached photo missing from disk: %s", file_path)
        return None
    try:
        with Image.open(file_path) as handle:
            handle.load()
            if "A" in handle.getbands():
                # A cut-out plate drawn on the photo board: flatten it onto
                # white rather than letting convert("RGB") fill the paper
                # with black.
                flattened = Image.new("RGB", handle.size, (255, 255, 255))
                flattened.paste(handle, mask=handle.convert("RGBA").split()[3])
                return flattened
            return handle.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        log.warning("could not read %s: %s", file_path, exc)
        return None


class BoardRenderer:
    def __init__(self, config: Config):
        self.config = config
        self.width = config.display.width
        self.height = config.display.height
        self.fonts = F.FontBook()

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

    def render(self, board: Board, now: datetime | None = None) -> Image.Image:
        now = self._local(now)
        stale = board.is_stale(self.config.cache.max_age_hours)

        photo = None
        if board.headline is not None:
            photo = _load_photo(
                board.headline_photo.path if board.headline_photo else None
            )

        canvas = new_canvas(self.width, self.height, self.config.palette)
        draw = ImageDraw.Draw(canvas)

        if board.headline is None or photo is None:
            # An image failure must not cost us the whole board.
            log.info("no usable photo; rendering the text-only layout")
            self._draw_text_only(draw, board, now, stale)
            return canvas

        self._draw_photo(canvas, draw, photo, board)
        self._draw_column(draw, board, now, stale, x=PHOTO_SIZE + PAD)
        return canvas

    def render_placeholder(self, message: str, detail: str = "") -> Image.Image:
        """Something to show when there is no cache at all yet."""
        canvas = new_canvas(self.width, self.height, self.config.palette)
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, self.width, 6), fill=int(Ink.RED))

        title_font = self.fonts.sized("sans_bold", 34)
        y = self.height // 2 - 60
        for line in F.wrap(message, title_font, self.width - 2 * PAD * 2, max_lines=2):
            draw.text((PAD * 2, y), line, font=title_font, fill=int(Ink.BLACK))
            y += F.line_height(title_font)
        if detail:
            y += GAP
            for line in F.wrap(detail, self.fonts.body, self.width - 2 * PAD * 2, 3):
                draw.text((PAD * 2, y), line, font=self.fonts.body, fill=int(Ink.BLACK))
                y += F.line_height(self.fonts.body) + 2
        return canvas

    # -- pieces ----------------------------------------------------------

    def _draw_photo(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        photo: Image.Image,
        board: Board,
    ) -> None:
        dithered = prepare_photo(
            photo, PHOTO_SIZE, self.height, self.config.image, self.config.palette
        )
        # Both images are palettised in the same index order, so this copies
        # ink indices straight across with no requantisation.
        canvas.paste(dithered, (0, 0))

        credit = board.headline_photo.credit_line if board.headline_photo else ""
        if not credit:
            return
        # Credit sits on the photo it belongs to, in a bar just wide enough
        # to carry it. It is a licence condition, not decoration.
        font = self.fonts.tiny
        text = F.truncate(credit, font, PHOTO_SIZE - 16)
        text_w = F.text_width(text, font)
        bar_h = F.line_height(font) + 4
        top = self.height - bar_h
        draw.rectangle((0, top, text_w + 12, self.height), fill=int(Ink.BLACK))
        draw.text((6, top + 2), text, font=font, fill=int(Ink.WHITE))

    def _draw_column(
        self,
        draw: ImageDraw.ImageDraw,
        board: Board,
        now: datetime,
        stale: bool,
        x: int,
    ) -> None:
        width = self.width - x - PAD
        y = PAD
        headline = board.headline
        assert headline is not None

        y = self._draw_masthead(draw, board, now, x, y, width)

        # Kicker: why this bird is on the wall today.
        kicker = "NOTABLE SIGHTING" if headline.notable else "SEEN NEARBY"
        kicker_ink = Ink.RED if headline.notable else Ink.GREEN
        draw.text((x, y), kicker, font=self.fonts.label, fill=int(kicker_ink))
        y += F.line_height(self.fonts.label) + 6

        y = self._draw_species_name(draw, headline, x, y, width)

        if headline.species.scientific_name:
            text = F.truncate(
                headline.species.scientific_name, self.fonts.scientific, width
            )
            draw.text((x, y), text, font=self.fonts.scientific, fill=int(Ink.BLACK))
            y += F.line_height(self.fonts.scientific) + GAP

        y = self._draw_provenance(draw, headline, now, x, y, width)

        # The rule in red separates "today's bird" from "everything else".
        y += 4
        draw.rectangle((x, y, x + width, y + 2), fill=int(Ink.RED))
        y += 2 + GAP + 4

        footer_top = self._draw_footer(draw, board, now, stale, x, width)
        self._draw_digest(draw, board, now, x, y, width, footer_top - GAP)

    def _draw_text_only(
        self,
        draw: ImageDraw.ImageDraw,
        board: Board,
        now: datetime,
        stale: bool,
    ) -> None:
        """Full-width fallback for when there is no usable photograph.

        Not a degraded version of the photo board -- a different board,
        laid out to look deliberate, because it may be up for six hours.
        """
        x = PAD
        full_width = self.width - 2 * PAD
        y = self._draw_masthead(draw, board, now, x, PAD, full_width)

        left_w = 400
        divider_x = x + left_w + PAD * 2
        right_x = divider_x + PAD
        right_w = self.width - right_x - PAD

        footer_top = self._draw_footer(draw, board, now, stale, x, full_width)
        draw.rectangle(
            (divider_x, y, divider_x + 2, footer_top - GAP), fill=int(Ink.RED)
        )

        headline = board.headline
        left_y = y
        if headline is None:
            title_font = self.fonts.sized("sans_bold", 34)
            draw.text(
                (x, left_y),
                "Nothing reported",
                font=title_font,
                fill=int(Ink.BLACK),
            )
            left_y += F.line_height(title_font) + GAP
            for line in F.wrap(
                f"No sightings came back for {board.region_name} on the last "
                "refresh. The list opposite is the most recent data held.",
                self.fonts.body,
                left_w,
                max_lines=4,
            ):
                draw.text((x, left_y), line, font=self.fonts.body, fill=int(Ink.BLACK))
                left_y += F.line_height(self.fonts.body)
        else:
            kicker = "NOTABLE SIGHTING" if headline.notable else "SEEN NEARBY"
            kicker_ink = Ink.RED if headline.notable else Ink.GREEN
            draw.text((x, left_y), kicker, font=self.fonts.label, fill=int(kicker_ink))
            left_y += F.line_height(self.fonts.label) + 6
            left_y = self._draw_species_name(
                draw, headline, x, left_y, left_w, sizes=(64, 56, 48, 42, 36, 30)
            )
            if headline.species.scientific_name:
                font = self.fonts.sized("serif_italic", 21)
                draw.text(
                    (x, left_y),
                    F.truncate(headline.species.scientific_name, font, left_w),
                    font=font,
                    fill=int(Ink.BLACK),
                )
                left_y += F.line_height(font) + GAP
            left_y = self._draw_provenance(draw, headline, now, x, left_y, left_w)
            if headline.species.family:
                left_y += GAP
                draw.text(
                    (x, left_y),
                    F.truncate(headline.species.family, self.fonts.small, left_w),
                    font=self.fonts.small,
                    fill=int(Ink.BLUE),
                )
                left_y += F.line_height(self.fonts.small)

        if board.checklist_note:
            # Only the text-only board has the room for this, and it fills
            # what would otherwise be an awkward hole under the headline.
            left_y += GAP * 2
            for line in F.wrap(board.checklist_note, self.fonts.body, left_w, 3):
                draw.text((x, left_y), line, font=self.fonts.body, fill=int(Ink.BLACK))
                left_y += F.line_height(self.fonts.body)

        # No photo means a taller column, so let the digest use all of it
        # rather than stopping at the count tuned for the narrow layout.
        self._draw_digest(
            draw, board, now, right_x, y, right_w, footer_top - GAP, limit=len(board.also_seen)
        )

    def _draw_masthead(
        self,
        draw: ImageDraw.ImageDraw,
        board: Board,
        now: datetime,
        x: int,
        y: int,
        width: int,
    ) -> int:
        region = F.truncate(board.region_name.upper(), self.fonts.label, width * 0.6)
        draw.text((x, y), region, font=self.fonts.label, fill=int(Ink.BLACK))

        date_text = now.strftime("%-d %b").upper()
        date_w = F.text_width(date_text, self.fonts.label)
        draw.text(
            (x + width - date_w, y), date_text, font=self.fonts.label, fill=int(Ink.BLUE)
        )
        y += F.line_height(self.fonts.label) + 4
        draw.rectangle((x, y, x + width, y), fill=int(Ink.BLACK))
        return y + GAP + 2

    def _draw_species_name(
        self,
        draw: ImageDraw.ImageDraw,
        headline: Sighting,
        x: int,
        y: int,
        width: int,
        sizes: tuple[int, ...] = (42, 38, 34, 30, 26, 22),
    ) -> int:
        """Largest size that gets the name into at most two lines.

        Species names run from "Robin" to "Eurasian Reed Warbler", so a
        fixed size either wastes the column or overflows it.
        """
        name = headline.species.common_name
        smallest = self.fonts.sized("sans_bold", min(sizes))
        chosen_font = smallest
        chosen_lines = F.wrap(name, smallest, width, max_lines=2)
        for size in sorted(sizes, reverse=True):
            font = self.fonts.sized("sans_bold", size)
            lines = F.wrap(name, font, width)
            if len(lines) <= 2:
                chosen_font, chosen_lines = font, lines
                break

        for line in chosen_lines:
            draw.text((x, y), line, font=chosen_font, fill=int(Ink.BLACK))
            y += F.line_height(chosen_font)
        return y + 2

    def _draw_provenance(
        self,
        draw: ImageDraw.ImageDraw,
        headline: Sighting,
        now: datetime,
        x: int,
        y: int,
        width: int,
    ) -> int:
        when = _fmt_when(headline.observed_at, now)
        if when:
            count = _fmt_count(headline)
            line = f"{when} · {count} seen" if count else when
            draw.text((x, y), line, font=self.fonts.body_bold, fill=int(Ink.BLACK))
            y += F.line_height(self.fonts.body_bold) + 2

        if headline.location:
            for line in F.wrap(headline.location, self.fonts.small, width, max_lines=2):
                draw.text((x, y), line, font=self.fonts.small, fill=int(Ink.BLACK))
                y += F.line_height(self.fonts.small)
        return y

    def _draw_digest(
        self,
        draw: ImageDraw.ImageDraw,
        board: Board,
        now: datetime,
        x: int,
        y: int,
        width: int,
        bottom: int,
        limit: int | None = None,
    ) -> None:
        """The "also seen nearby" list, trimmed to whatever room is left."""
        draw.text((x, y), "ALSO SEEN NEARBY", font=self.fonts.label, fill=int(Ink.BLACK))
        y += F.line_height(self.fonts.label) + 6

        font = self.fonts.small
        row_h = F.line_height(font) + 3
        room = max(0, int((bottom - y) // row_h))
        limit = min(
            self.config.board.also_seen_count if limit is None else limit, room
        )
        if limit <= 0:
            return

        for sighting in board.also_seen[:limit]:
            day = _fmt_day(sighting.observed_at, now)
            day_w = F.text_width(day, self.fonts.tiny) if day else 0
            name = F.truncate(
                sighting.species.common_name, font, width - day_w - GAP
            )
            draw.text((x, y), name, font=font, fill=int(Ink.BLACK))
            if day:
                draw.text(
                    (x + width - day_w, y + 2),
                    day,
                    font=self.fonts.tiny,
                    fill=int(Ink.BLUE),
                )
            y += row_h

    def _draw_footer(
        self,
        draw: ImageDraw.ImageDraw,
        board: Board,
        now: datetime,
        stale: bool,
        x: int,
        width: int,
    ) -> int:
        """Bottom-anchored. Returns its top edge so the digest can stop there."""
        font = self.fonts.tiny
        line_h = F.line_height(font)
        top = self.height - PAD - line_h

        if stale:
            # Never present week-old sightings as if they were current.
            age = board.age_hours(None)
            text = f"DATA STALE · {age / 24:.0f}d old · check the fetcher"
            ink = Ink.RED
        else:
            stamp = board.generated_at.astimezone(
                self.config.region.tzinfo
            ).strftime("%H:%M")
            text = f"Updated {stamp} · data from eBird"
            ink = Ink.BLACK

        draw.text((x, top), F.truncate(text, font, width), font=font, fill=int(ink))
        return top


def render_board(board: Board, config: Config, now: datetime | None = None) -> Image.Image:
    return BoardRenderer(config).render(board, now)
