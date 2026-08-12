"""Lift the bird off the paper: background removal for lithographic plates.

A scanned Keulemans plate is a bird floating on cream paper, usually with a
printed caption underneath ("ROBIN. / Erithacus rubecula (Linn.)"), the
lithographer's signature in the corner, and whatever the scanner caught at
the edges of the page. We want the bird and the branch it is standing on,
and nothing else.

Three ideas do the whole job:

1. **The background is paper-coloured and touches the edge.** So the mask is
   not "everything near the paper colour" -- that would eat the white in a
   gull's wing -- but "everything near the paper colour that is *connected*
   to the border". Enclosed whites survive.

2. **The caption is small and separate.** Once the paper is gone, what is
   left is a handful of blobs: one big one (the bird), sometimes a second
   (a pair, or a perch drawn clear of the bird), and thirty tiny ones (the
   letters of the caption, the signature, specks of foxing). Keeping only
   the components within a fraction of the largest one throws the text away
   without ever having to recognise it as text.

3. **It has to admit when it failed.** Some plates are printed inside a
   ruled frame, and the flood fill then stops at the frame and keeps the
   whole page. `CutoutResult.coverage` catches that -- a cutout that fills
   most of its own bounding box is a page, not a bird -- and the caller
   decides whether to keep it.

Build-time only: this runs on a laptop and needs numpy and scipy. Nothing
here is imported on the Pi, which only ever reads finished cutouts out of
the database.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

# Fraction of the image width sampled at each edge to estimate paper colour.
_BORDER_BAND = 0.04
# How far from the paper colour a pixel may sit and still count as paper.
# Generous: scanned paper is not flat, and the tone drifts across a page.
_PAPER_TOLERANCE = 46.0
# Keep foreground blobs at least this fraction of the largest blob's area.
# Big enough to drop caption letters, small enough to keep a second bird.
_KEEP_RATIO = 0.05
# A blob smaller than this fraction of the frame is a speck whatever else
# is going on -- used when the largest blob is itself small.
_MIN_ABSOLUTE_AREA = 0.0008
# Margin left around the subject when cropping, as a fraction of its size.
_CROP_MARGIN = 0.03


# Above this share of its own bounding box, what came back is a rectangle
# rather than a bird: either a plate whose background Keulemans painted in
# (his "Onze vogels" birds mostly sit in a landscape), or a cutout that
# failed. Calibrated against real plates: a bird on bare paper lands
# between 0.45 and 0.65, a painted scene between 0.73 and 1.0.
SCENE_COVERAGE = 0.70
FAILED_COVERAGE = 0.97


@dataclass(frozen=True)
class CutoutResult:
    """A finished cutout plus the numbers needed to judge it."""

    image: Image.Image
    coverage: float
    """Share of the cropped frame the subject actually fills. Low is normal
    (a bird is not a rectangle); very high means the flood fill never got
    in and we have kept the whole page."""

    kept_blobs: int
    dropped_blobs: int
    paper_rgb: tuple[int, int, int]
    reseeded: bool
    """True if the border pass failed and we seeded from inside a frame."""

    @property
    def is_scene(self) -> bool:
        """The plate has its own background, so there is none to remove.

        Not a failure -- these are fine drawings -- but on a board built
        out of white space they arrive as a dithered rectangle, so a plate
        that cuts to bare paper is always worth more.
        """
        return self.coverage > SCENE_COVERAGE

    @property
    def failed(self) -> bool:
        """Nothing was removed at all: a ruled frame, or a photograph."""
        return self.kept_blobs == 0 or self.coverage > FAILED_COVERAGE

    @property
    def suspect(self) -> bool:
        """Worth a human eye before it goes in the database."""
        return self.failed or self.is_scene


def _paper_colour(pixels: np.ndarray) -> np.ndarray:
    """Median colour of a band around the edge of the scan.

    Median rather than mean: a dark scanner gutter down one side shifts a
    mean badly, and shifts a median not at all.
    """
    height, width = pixels.shape[:2]
    band_y = max(2, int(height * _BORDER_BAND))
    band_x = max(2, int(width * _BORDER_BAND))
    edges = np.concatenate(
        [
            pixels[:band_y].reshape(-1, 3),
            pixels[-band_y:].reshape(-1, 3),
            pixels[:, :band_x].reshape(-1, 3),
            pixels[:, -band_x:].reshape(-1, 3),
        ]
    )
    return np.median(edges, axis=0)


def _paper_mask(pixels: np.ndarray, paper: np.ndarray, tolerance: float) -> np.ndarray:
    """Pixels close enough to the paper colour to be paper."""
    delta = pixels.astype(np.float32) - paper.astype(np.float32)
    return np.sqrt((delta * delta).sum(axis=2)) < tolerance


def _connected_to(mask: np.ndarray, seeds: np.ndarray) -> np.ndarray:
    """The parts of `mask` reachable from any True in `seeds`."""
    labels, count = ndimage.label(mask)
    if count == 0:
        return np.zeros_like(mask)
    touched = np.unique(labels[seeds & (labels > 0)])
    if touched.size == 0:
        return np.zeros_like(mask)
    return np.isin(labels, touched)


def _border_seeds(shape: tuple[int, int]) -> np.ndarray:
    seeds = np.zeros(shape, dtype=bool)
    seeds[0, :] = seeds[-1, :] = True
    seeds[:, 0] = seeds[:, -1] = True
    return seeds


def _inset_seeds(shape: tuple[int, int], inset: float = 0.04) -> np.ndarray:
    """A rectangle of seeds just inside the edge.

    For plates printed inside a ruled frame: the border pass stops dead at
    the frame line, so we start again from inside it.
    """
    height, width = shape
    dy, dx = max(1, int(height * inset)), max(1, int(width * inset))
    seeds = np.zeros(shape, dtype=bool)
    seeds[dy, dx:-dx] = seeds[-dy, dx:-dx] = True
    seeds[dy:-dy, dx] = seeds[dy:-dy, -dx] = True
    return seeds


def _largest_blobs(foreground: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Keep the subject, drop the caption.

    Returns the kept mask and how many blobs were kept and dropped.
    """
    labels, count = ndimage.label(foreground)
    if count == 0:
        return foreground, 0, 0

    areas = ndimage.sum_labels(foreground, labels, index=np.arange(1, count + 1))
    biggest = float(areas.max())
    floor = max(biggest * _KEEP_RATIO, foreground.size * _MIN_ABSOLUTE_AREA)

    height, width = foreground.shape
    edge = np.zeros_like(foreground)
    thickness = max(1, int(min(height, width) * 0.01))
    edge[:thickness, :] = edge[-thickness:, :] = True
    edge[:, :thickness] = edge[:, -thickness:] = True
    on_edge = set(np.unique(labels[edge & (labels > 0)]).tolist())
    main = int(np.argmax(areas)) + 1

    keep = []
    for index, area in enumerate(areas, start=1):
        if area < floor:
            continue
        # A blob running off the edge of the scan is the page gutter or a
        # scanner shadow, not the bird -- unless it *is* the biggest blob.
        if index in on_edge and index != main:
            continue
        keep.append(index)

    kept = np.isin(labels, keep)
    return kept, len(keep), count - len(keep)


def _crop_box(mask: np.ndarray) -> tuple[int, int, int, int]:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    margin_y = int((bottom - top) * _CROP_MARGIN)
    margin_x = int((right - left) * _CROP_MARGIN)
    height, width = mask.shape
    return (
        max(0, left - margin_x),
        max(0, top - margin_y),
        min(width, right + margin_x),
        min(height, bottom + margin_y),
    )


def cut_out(
    image: Image.Image,
    tolerance: float = _PAPER_TOLERANCE,
    feather: float = 0.8,
) -> CutoutResult:
    """Remove the paper behind a plate. Returns an RGBA image, cropped.

    The alpha channel is soft-edged by `feather` pixels: a hard mask on a
    lithograph's stippled outline looks cut out with scissors, and the
    panel's dithering exaggerates it.
    """
    rgb = image.convert("RGB")
    pixels = np.asarray(rgb)
    paper = _paper_colour(pixels)
    paper_mask = _paper_mask(pixels, paper, tolerance)

    background = _connected_to(paper_mask, _border_seeds(paper_mask.shape))
    foreground = ~background
    kept, n_kept, n_dropped = _largest_blobs(foreground)
    reseeded = False

    # Ruled frame: the fill never got past the border, so almost nothing
    # was removed. Start again from inside the frame.
    if kept.mean() > 0.72:
        inner = _connected_to(paper_mask, _inset_seeds(paper_mask.shape))
        if inner.any():
            kept, n_kept, n_dropped = _largest_blobs(~inner)
            reseeded = True

    if not kept.any():
        return CutoutResult(
            image=rgb.convert("RGBA"),
            coverage=1.0,
            kept_blobs=0,
            dropped_blobs=n_dropped,
            paper_rgb=tuple(int(c) for c in paper),  # type: ignore[arg-type]
            reseeded=reseeded,
        )

    # Fill enclosed paper -- the gap between a wing and a body reads as
    # background from the outside, but a hole in the bird from the inside.
    kept = ndimage.binary_fill_holes(kept)

    alpha = Image.fromarray((kept * 255).astype(np.uint8), mode="L")
    if feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))

    cut = rgb.convert("RGBA")
    cut.putalpha(alpha)
    box = _crop_box(kept)
    cut = cut.crop(box)

    cropped_area = (box[2] - box[0]) * (box[3] - box[1])
    coverage = float(kept.sum()) / max(1, cropped_area)
    return CutoutResult(
        image=cut,
        coverage=coverage,
        kept_blobs=n_kept,
        dropped_blobs=n_dropped,
        paper_rgb=tuple(int(c) for c in paper),  # type: ignore[arg-type]
        reseeded=reseeded,
    )


def fit_within(image: Image.Image, longest_edge: int) -> Image.Image:
    """Scale down so the longest side is at most `longest_edge`."""
    width, height = image.size
    longest = max(width, height)
    if longest <= longest_edge:
        return image
    scale = longest_edge / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)
