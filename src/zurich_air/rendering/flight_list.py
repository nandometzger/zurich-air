"""The flight list: one two-line row per featured aircraft, numbered like the radar.

    ▮1 SWR8           ▲ 5400 FT
       Swiss · 777          → JFK

The callsign is the aircraft's identity and is never cut: beside a long one, the altitude
moves up close, the callsign steps down a size, and then the altitude gives way
(``▲6900 FT`` → ``▲6900`` → ``6900``; see :func:`fit_first_line`). The right end of the
second line says where the aircraft is going: its verified route (``DXB →`` arriving from
Dubai, ``→ JFK`` departing for New York, ``LHR → IST`` passing over), else ``ARR`` or
``DEP`` for an arrival or departure, otherwise an arrow along its track. The description
gives way by meaning (``Swiss · 777-300ER`` → ``Swiss · 777`` → ``777-300ER`` → ``777``),
never with an ellipsis. Missing fields are left out; nothing is guessed, and a route is
only shown once the aircraft is seen flying it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing.phase import Phase
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.rendering import formatting
from zurich_air.rendering.fonts import Face, cap_height, draw_text, font, text_width
from zurich_air.rendering.layout import (
    BADGE_H,
    CALLSIGN,
    CALLSIGN_SPACE,
    LABEL_SPACE,
    LINE_GAP,
    LONG_CALLSIGN,
    MAX_ROW_PITCH,
    MIN_SPACE,
    RADAR_GAP,
    SMALL,
    SYMBOL_GAP,
    TEXT_INDENT,
    TRACKING,
    TREND_SIZE,
    Box,
)
from zurich_air.rendering.symbols import ARROW_SIZE, draw_arrow, draw_badge, draw_trend

PHASE_LABELS: dict[Phase, str] = {"arrival": "ARR", "departure": "DEP"}


def draw_flight_list(
    image: Image.Image,
    box: Box,
    featured: Sequence[Aircraft],
    config: Config,
    phases: Mapping[str, Phase] | None = None,
    *,
    routes: Mapping[str, VerifiedRoute] | None = None,
    waiting: bool = False,
) -> None:
    """The rows; with *waiting* (no usable data) a calm note instead.

    *phases* and *routes* are by ICAO address.
    """
    draw = ImageDraw.Draw(image)
    if waiting:
        _draw_note(draw, box, "WAITING FOR DATA")
        return
    if not featured:
        within = f"within {formatting.distance(config.radar.range_km).lower()}"
        _draw_note(draw, box, "NO AIRCRAFT", within)
        return
    phases, routes = phases or {}, routes or {}
    cap = cap_height(font(CALLSIGN, bold=True))
    top = box.top + RADAR_GAP  # first cap line level with the top of the radar ring
    bottom = box.bottom - RADAR_GAP
    pitch = MAX_ROW_PITCH
    if len(featured) > 1:
        pitch = min(MAX_ROW_PITCH, (bottom - top - cap - LINE_GAP) // (len(featured) - 1))
    for i, aircraft in enumerate(featured):
        baseline = top + cap + i * pitch
        where = routes.get(aircraft.icao) or phases.get(aircraft.icao)
        _draw_row(image, draw, box, baseline, i + 1, aircraft, where, config)


def _draw_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: Box,
    baseline: int,
    number: int,
    aircraft: Aircraft,
    where: VerifiedRoute | Phase | None,
    config: Config,
) -> None:
    """One row; *where* is the aircraft's verified route, else its phase, if known."""
    small = font(SMALL)
    cap = cap_height(font(CALLSIGN, bold=True))  # every row's, whatever size its label
    text_left = box.left + TEXT_INDENT
    draw_badge(draw, box.left, baseline - cap + (cap - BADGE_H) // 2, number)

    # Line 1: the label, always in full; altitude and climb/descend triangle flush right.
    line = fit_first_line(aircraft, config.display.altitude_units, box.right - text_left)
    right = box.right
    if line.altitude:
        right -= draw_text(draw, right, baseline, line.altitude, small, align="right")
    if line.climbing is not None:
        centre = right - SYMBOL_GAP - TREND_SIZE
        draw_trend(draw, centre, baseline - 2, line.climbing, TREND_SIZE)
    draw_text(draw, text_left, baseline, aircraft.label, line.face)

    # Line 2: operator and model; where it is going flush right.
    baseline += LINE_GAP
    right = box.right
    if isinstance(where, VerifiedRoute):
        label = formatting.route_label(where)
        right -= draw_text(draw, right, baseline, label, small, tracking=TRACKING, align="right")
        right -= MIN_SPACE  # the arrow already sets it apart from the model name
    elif where is not None:
        tag = PHASE_LABELS[where]
        right -= draw_text(draw, right, baseline, tag, small, tracking=TRACKING, align="right")
        right -= LABEL_SPACE
    elif aircraft.track_deg is not None:
        draw_arrow(image, right - ARROW_SIZE, baseline - ARROW_SIZE, aircraft.track_deg)
        right -= ARROW_SIZE + MIN_SPACE
    if aircraft.emergency:
        draw_text(draw, text_left, baseline, "EMERGENCY", font(SMALL, bold=True))
        return
    room = right - text_left
    text = next((t for t in formatting.descriptions(aircraft) if text_width(t, small) <= room), "")
    draw_text(draw, text_left, baseline, text, small)


@dataclass(frozen=True)
class FirstLine:
    """How the first line of a row is set: the label in *face*, then flush right a
    climb (▲) or descend (▼) triangle, if *climbing* is not None, and *altitude*."""

    face: Face
    altitude: str = ""
    climbing: bool | None = None

    def width(self, label: str) -> int:
        """Ink width of the whole line, with the least space between label and altitude."""
        width = text_width(label, self.face)
        if self.altitude:
            width += CALLSIGN_SPACE + text_width(self.altitude, font(SMALL))
        if self.climbing is not None:
            width += SYMBOL_GAP + 2 * TREND_SIZE - 1
        return width


def fit_first_line(aircraft: Aircraft, units: str, room: int) -> FirstLine:
    """The most complete first line that fits into *room* pixels.

    The label is the aircraft's identity, so it is always shown whole. The rest makes
    way, in this order:

    1. the space before the altitude closes up, down to CALLSIGN_SPACE;
    2. the label steps down from CALLSIGN to LONG_CALLSIGN, on the same baseline and
       left edge;
    3. the altitude drops its unit, then its thousands space (:func:`formatting.altitudes`),
       each form tried with the label back at CALLSIGN first;
    4. the climb/descend triangle goes, and the altitude starts over from its full form.

    Measured on the 400 x 300 display, every airline-style callsign of up to 8 characters
    (the ADS-B maximum) keeps its altitude, except the very widest, such as ``WWW888WW``,
    beside five metric digits: that one stands alone rather than run into its altitude.
    """
    label = aircraft.label
    forms = formatting.altitudes(aircraft.altitude_ft, units, on_ground=aircraft.on_ground)
    trend = formatting.vertical_trend(aircraft.vertical_rate_fpm)
    climbing = None
    if forms and trend in ("climb", "descend") and not aircraft.on_ground:
        climbing = trend == "climb"
    rungs = [(form, climbing) for form in forms]
    if climbing is not None:
        rungs += [(form, None) for form in forms]
    rungs.append(("", None))  # the label alone
    faces = font(CALLSIGN, bold=True), font(LONG_CALLSIGN, bold=True)
    for altitude, climbs in rungs:
        for face in faces:
            line = FirstLine(face, altitude, climbs)
            if line.width(label) <= room:
                return line
    return FirstLine(faces[-1])  # longer than any callsign: drawn in full all the same


def _draw_note(draw: ImageDraw.ImageDraw, box: Box, title: str, detail: str = "") -> None:
    middle = (box.top + box.bottom) // 2
    draw_text(draw, box.left, middle - 2, title, font(SMALL, bold=True), tracking=TRACKING)
    if detail:
        draw_text(draw, box.left, middle + 12, detail, font(SMALL))
