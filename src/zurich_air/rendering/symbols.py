"""Small vector symbols drawn pixel by pixel: aircraft, badges, arrows, triangles.

Rotated shapes are rasterised with 8x supersampling and thresholded, which keeps them symmetric
and recognisable at 7 to 15 px — better than drawing the polygon directly at 1 bit.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from zurich_air.rendering.fonts import cap_height, draw_text, font, text_width
from zurich_air.rendering.layout import TINY

BLACK, WHITE = 0, 255
_SUPERSAMPLE = 8


def _mirrored(half: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    """Close a right-hand outline (nose first, y pointing backwards) into a full shape."""
    return (*half, *((-x, y) for x, y in reversed(half) if x != 0.0))


# Unit outlines pointing north (nose at y = -1), x to the right.
AIRLINER = _mirrored(
    [
        (0.0, -1.0),
        (0.13, -0.88),
        (0.15, -0.60),
        (0.15, -0.22),
        (0.95, 0.08),
        (0.95, 0.30),
        (0.15, 0.16),
        (0.12, 0.58),
        (0.45, 0.78),
        (0.45, 0.98),
        (0.0, 0.90),
    ]
)
DART = _mirrored([(0.0, -1.0), (0.78, 0.85), (0.0, 0.42)])
ARROW = _mirrored([(0.0, -1.0), (0.75, -0.15), (0.22, -0.15), (0.22, 1.0), (0.0, 1.0)])


class Sprite:
    """A rasterised symbol: a black shape plus a one-pixel white halo around it."""

    def __init__(self, shape: Image.Image) -> None:
        self.shape = shape
        self.halo = shape.convert("L").filter(ImageFilter.MaxFilter(3)).convert("1")
        self.size = shape.width

    def stamp(self, image: Image.Image, cx: int, cy: int, *, halo: bool = True) -> None:
        """Draw the sprite centred on pixel (cx, cy), first clearing its halo."""
        origin = (cx - self.size // 2, cy - self.size // 2)
        if halo:
            image.paste(WHITE, origin, self.halo)
        image.paste(BLACK, origin, self.shape)


Transform = Callable[[float, float], tuple[float, float]]


def _rasterise(
    length: float, angle: int, paint: Callable[[ImageDraw.ImageDraw, Transform, float], None]
) -> Sprite:
    """Paint unit coordinates (nose at y = -1) scaled to *length* px and rotated by *angle*."""
    canvas = (math.ceil(length) + 4) | 1  # odd, so the centre falls on a pixel centre
    big = Image.new("L", (canvas * _SUPERSAMPLE, canvas * _SUPERSAMPLE), 0)
    centre = canvas * _SUPERSAMPLE / 2
    scale = length / 2 * _SUPERSAMPLE
    cos_a, sin_a = math.cos(math.radians(angle)), math.sin(math.radians(angle))

    def transform(x: float, y: float) -> tuple[float, float]:
        return centre + scale * (x * cos_a - y * sin_a), centre + scale * (x * sin_a + y * cos_a)

    paint(ImageDraw.Draw(big), transform, scale)
    small = big.resize((canvas, canvas), Image.Resampling.BOX)
    return Sprite(small.point(lambda v: 255 if v >= 110 else 0).convert("1"))


@lru_cache(maxsize=1024)
def sprite(outline: tuple[tuple[float, float], ...], length: float, angle: int) -> Sprite:
    """*outline* scaled to *length* px nose to tail, rotated clockwise by *angle* degrees."""

    def paint(draw: ImageDraw.ImageDraw, transform: Transform, scale: float) -> None:
        draw.polygon([transform(x, y) for x, y in outline], fill=255)

    return _rasterise(length, angle, paint)


@lru_cache(maxsize=512)
def helicopter(length: float, angle: int) -> Sprite:
    """Top view of a helicopter: body and tail boom inside the rotor disc."""

    def paint(draw: ImageDraw.ImageDraw, transform: Transform, scale: float) -> None:
        hx, hy = transform(0.0, -0.12)
        r = 0.78 * scale
        draw.ellipse((hx - r, hy - r, hx + r, hy + r), outline=255, width=round(0.13 * scale))
        draw.polygon([transform(x, y) for x, y in _HELICOPTER_BODY], fill=255)

    return _rasterise(length, angle, paint)


_HELICOPTER_BODY = _mirrored(
    [(0.0, -0.62), (0.22, -0.5), (0.27, -0.2), (0.18, 0.12), (0.08, 0.2), (0.08, 0.86),
     (0.26, 0.86), (0.26, 1.0), (0.0, 1.0)]
)  # fmt: skip


# Numbered badge: a solid black box with a white digit, used on the radar and in the list.
BADGE_W, BADGE_H = 9, 11


def draw_badge(draw: ImageDraw.ImageDraw, left: int, top: int, number: int) -> None:
    """A black BADGE_W x BADGE_H box at (left, top) with *number* centred in white."""
    digit = str(number)
    fnt = font(TINY)
    draw.rectangle((left, top, left + BADGE_W - 1, top + BADGE_H - 1), fill=BLACK)
    x = left + (BADGE_W - text_width(digit, fnt)) // 2
    baseline = top + (BADGE_H + cap_height(fnt)) // 2
    draw_text(draw, x, baseline, digit, fnt, fill=WHITE)


def draw_trend(draw: ImageDraw.ImageDraw, cx: int, baseline: int, up: bool, size: int = 4) -> int:
    """Solid climb (▲) or descend (▼) triangle, *size* rows tall, resting on *baseline*.

    Returns its width (2 * size - 1).
    """
    for row in range(size):
        half = row if up else size - 1 - row
        y = baseline - size + row
        draw.line((cx - half, y, cx + half, y), fill=BLACK)
    return 2 * size - 1


def draw_dot(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: int, filled: bool) -> None:
    """A round live indicator: filled when fresh, a ring when stale."""
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    if filled:
        draw.ellipse(box, fill=BLACK)
    else:
        draw.ellipse(box, outline=BLACK, width=1)
