"""The bundled B612 typeface and pixel-exact text helpers.

B612 was designed by Airbus for cockpit displays. Drawn onto a mode "1" image, Pillow
rasterises it without anti-aliasing, so every glyph lands on whole pixels.

Bold is B612 Regular struck twice, one pixel apart. B612-Bold.ttf ships without hinting
instructions, so FreeType's auto-hinter sets some letters (H, N, U, W, …) one pixel higher
than others at 9 to 15 px — a visible wobble in 1-bit. The Regular cut is properly hinted
and sits on a perfectly even baseline at 9, 10, 13 and 14 px, the sizes used here.

Text is placed by its baseline and by the edges of its actual ink, so left and right edges
line up exactly across rows regardless of each glyph's side bearings.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
ELLIPSIS = "…"
MIDDLE_DOT = "·"


@dataclass(frozen=True)
class Face:
    """B612 at one pixel size, optionally emboldened."""

    font: ImageFont.FreeTypeFont
    bold: bool = False

    @property
    def size(self) -> int:
        return int(self.font.size)

    def render(
        self, draw: ImageDraw.ImageDraw, x: int, baseline: int, text: str, fill: int
    ) -> None:
        draw.text((x, baseline), text, font=self.font, anchor="ls", fill=fill)
        if self.bold and text != MIDDLE_DOT:  # a doubled dot would read as a dash
            draw.text((x + 1, baseline), text, font=self.font, anchor="ls", fill=fill)


@cache
def _truetype(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / "B612-Regular.ttf"), size)


@cache
def font(size: int, *, bold: bool = False) -> Face:
    """B612 at *size* pixels (cached)."""
    return Face(_truetype(size), bold)


@cache
def cap_height(face: Face) -> int:
    """Height of a capital letter above the baseline, in pixels."""
    return -face.font.getbbox("H", anchor="ls")[1]


def text_width(text: str, face: Face, tracking: int = 0) -> int:
    """Ink width of *text* in pixels, letter-spaced by *tracking* (see :func:`draw_text`)."""
    return _layout(text, face, tracking)[1]


def draw_text(
    draw: ImageDraw.ImageDraw,
    x: int,
    baseline: int,
    text: str,
    face: Face,
    *,
    tracking: int = 0,
    align: Literal["left", "right"] = "left",
    fill: int = 0,
) -> int:
    """Draw *text* on *baseline* with its ink starting at *x* (or ending just before it).

    Letter-spaced text is set glyph by glyph. At these sizes B612's hinted side bearings
    are irregular (a "T" overhangs, an "N" fills its whole advance), so letters are spaced
    by their ink: every pair gets the same gap of ``1 + tracking`` pixels and a word space
    adds the space advance plus *tracking*. Returns the ink width.
    """
    origins, width = _layout(text, face, tracking)
    if align == "right":
        x -= width
    if not tracking:
        face.render(draw, x + origins[0], baseline, text, fill)
    else:
        for ch, origin in zip(text, origins, strict=True):
            if not ch.isspace():
                face.render(draw, x + origin, baseline, ch, fill)
    return width


def truncate(text: str, face: Face, max_width: int, tracking: int = 0) -> str:
    """*text* shortened with an ellipsis so that it fits into *max_width* pixels."""
    if text_width(text, face, tracking) <= max_width:
        return text
    for end in range(len(text) - 1, 0, -1):
        candidate = text[:end].rstrip(" ·-") + ELLIPSIS
        if text_width(candidate, face, tracking) <= max_width:
            return candidate
    return ""


def _layout(text: str, face: Face, tracking: int) -> tuple[list[int], int]:
    """Origins (relative to the left ink edge) and the total ink width of *text*.

    Untracked text is drawn in one piece and has a single origin.
    """
    if not text.strip():
        return [0] * max(1, len(text)), 0
    if not tracking:
        left, right = _ink_extent(face, text)
        return [-left], right - left
    space = round(face.font.getlength(" ", mode="1")) + tracking
    origins: list[int] = []
    edge: int | None = None  # right edge of the previous glyph's ink
    pending = 0  # word space owed before the next glyph
    for ch in text:
        if ch.isspace():
            pending += space
            origins.append(0)
            continue
        left, right = _ink_extent(face, ch)
        origin = -left if edge is None else edge + (pending or 1 + tracking) - left
        origins.append(origin)
        edge, pending = origin + right, 0
    return origins, edge or 0


@lru_cache(maxsize=4096)
def _ink_extent(face: Face, text: str) -> tuple[int, int]:
    """Horizontal extent (left, right exclusive) of the rendered pixels of *text*."""
    size = face.size
    width = round(face.font.getlength(text, mode="1")) + 2 * size
    probe = Image.new("1", (width, 3 * size), 0)
    face.render(ImageDraw.Draw(probe), size, 2 * size, text, 255)
    bbox = probe.getbbox()
    if bbox is None:
        return 0, 0
    return bbox[0] - size, bbox[2] - size
