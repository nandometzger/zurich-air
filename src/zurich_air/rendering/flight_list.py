"""The flight list: one two-line row per featured aircraft, numbered like the radar.

    ▮1 SWR008          ▲ 5900 FT
       Swiss · 777-300ER       ↙

Missing fields are left out; nothing is guessed.
"""

from __future__ import annotations

from collections.abc import Sequence

from PIL import Image, ImageDraw

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.rendering import formatting
from zurich_air.rendering.fonts import cap_height, draw_text, font, truncate
from zurich_air.rendering.layout import CALLSIGN, RADAR_GAP, SMALL, TRACKING, Box
from zurich_air.rendering.symbols import ARROW, BADGE_H, BADGE_W, draw_badge, draw_trend, sprite

LINE_GAP = 14  # callsign baseline to the baseline of the second line
MAX_ROW_PITCH = 49
TEXT_INDENT = BADGE_W + 6
ARROW_LENGTH = 9
TREND_SIZE = 4  # rows of the climb/descend triangle
GAP = 3  # between a symbol and its text
MIN_SPACE = 8  # between the left and right parts of a line


def draw_flight_list(
    image: Image.Image, box: Box, featured: Sequence[Aircraft], config: Config
) -> None:
    draw = ImageDraw.Draw(image)
    if not featured:
        _draw_empty(draw, box, config)
        return
    cap = cap_height(font(CALLSIGN, bold=True))
    top = box.top + RADAR_GAP  # first cap line level with the top of the radar ring
    bottom = box.bottom - RADAR_GAP
    pitch = MAX_ROW_PITCH
    if len(featured) > 1:
        pitch = min(MAX_ROW_PITCH, (bottom - top - cap - LINE_GAP) // (len(featured) - 1))
    for i, aircraft in enumerate(featured):
        _draw_row(image, draw, box, top + cap + i * pitch, i + 1, aircraft, config)


def _draw_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: Box,
    baseline: int,
    number: int,
    aircraft: Aircraft,
    config: Config,
) -> None:
    callsign_face = font(CALLSIGN, bold=True)
    small = font(SMALL)
    cap = cap_height(callsign_face)
    text_left = box.left + TEXT_INDENT
    draw_badge(draw, box.left, baseline - cap + (cap - BADGE_H) // 2, number)

    # Line 1: callsign; altitude and climb/descend triangle flush right.
    right = box.right
    altitude = formatting.altitude(
        aircraft.altitude_ft, config.display.altitude_units, on_ground=aircraft.on_ground
    )
    if altitude:
        right -= draw_text(draw, right, baseline, altitude, small, align="right")
        trend = formatting.vertical_trend(aircraft.vertical_rate_fpm)
        if trend in ("climb", "descend") and not aircraft.on_ground:
            centre = right - GAP - TREND_SIZE
            right -= GAP + draw_trend(draw, centre, baseline - 2, trend == "climb", TREND_SIZE)
        right -= MIN_SPACE
    label = truncate(aircraft.label, callsign_face, right - text_left)
    draw_text(draw, text_left, baseline, label, callsign_face)

    # Line 2: operator and model; an arrow along the track flush right.
    baseline = baseline + LINE_GAP
    right = box.right
    if aircraft.track_deg is not None:
        arrow = sprite(ARROW, ARROW_LENGTH, round(aircraft.track_deg) % 360)
        middle = baseline - (cap_height(small) + 1) // 2
        arrow.stamp(image, right - ARROW_LENGTH // 2 - 1, middle, halo=False)
        right -= ARROW_LENGTH + MIN_SPACE
    if aircraft.emergency:
        text, face = "EMERGENCY", font(SMALL, bold=True)
    else:
        text, face = formatting.description(aircraft), small
    draw_text(draw, text_left, baseline, truncate(text, face, right - text_left), face)


def _draw_empty(draw: ImageDraw.ImageDraw, box: Box, config: Config) -> None:
    middle = (box.top + box.bottom) // 2
    draw_text(draw, box.left, middle - 2, "NO AIRCRAFT", font(SMALL, bold=True), tracking=TRACKING)
    within = f"within {formatting.distance(config.radar.range_km).lower()}"
    draw_text(draw, box.left, middle + 12, within, font(SMALL))
