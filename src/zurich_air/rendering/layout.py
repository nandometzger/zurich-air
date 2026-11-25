"""Screen geometry, spacing and the type scale, derived from the display size.

At 400 x 300 px::

    12 ┌ ZURICH · LIVE AIR TRAFFIC ───────────────── ● 15:42 ┐   header, baseline y=22
    30 ├──────────────────────────────────────────────────────┤   hairline
       │   ╭──── radar ────╮      ▮1 UAE87        ▼ 2800 FT   │
       │   │   r = 112 px  │         Emirates · A380      ARR │   flight list
       │   ╰───────────────╯      ...                         │
   272 ├──────────────────────────────────────────────────────┤   hairline
   288 └ 17 AIRCRAFT · 60 KM ───────────────── SYNTHETIC DATA ┘   footer baseline
"""

from __future__ import annotations

from dataclasses import dataclass

MARGIN = 12
RULE_GAP = 8  # between a text line and its hairline rule
RADAR_GAP = 8  # between the hairlines and the radar's outer ring
COLUMN_GAP = 14  # between the radar and the flight list

# Type scale: pixel sizes at which B612 Regular has an even baseline (see fonts.py).
TITLE = 13  # header, bold
CALLSIGN = 14  # flight list, bold
SMALL = 10  # altitudes, secondary lines, footer
TINY = 9  # radar labels, badge digits
TRACKING = 1  # extra letter-spacing for uppercase labels

_TITLE_CAP = 10  # cap height of B612 Bold at TITLE size
_SMALL_CAP = 8  # cap height of B612 Regular at SMALL size

# Numbered badges, on the radar and in the flight list.
BADGE_W, BADGE_H = 9, 11

# Flight list.
LINE_GAP = 14  # callsign baseline to the baseline of the second line
MAX_ROW_PITCH = 49
TEXT_INDENT = BADGE_W + 6
TREND_SIZE = 4  # rows of the climb/descend triangle
SYMBOL_GAP = 3  # between a symbol and its text
MIN_SPACE = 8  # between the left and right parts of a line
LABEL_SPACE = 12  # before ARR / DEP, which would otherwise read as part of the model name


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int  # exclusive
    bottom: int  # exclusive

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    margin: int
    header_baseline: int
    header_rule_y: int
    footer_rule_y: int
    footer_baseline: int
    radar_cx: int
    radar_cy: int
    radar_radius: int
    flight_list: Box

    @classmethod
    def for_size(cls, width: int, height: int) -> Layout:
        m = MARGIN
        header_baseline = m + _TITLE_CAP
        header_rule_y = header_baseline + RULE_GAP
        footer_baseline = height - m
        footer_rule_y = footer_baseline - _SMALL_CAP - RULE_GAP
        top, bottom = header_rule_y + 1, footer_rule_y  # main area, bottom exclusive
        radius = (bottom - top) // 2 - RADAR_GAP
        cy = (top + bottom) // 2
        cx = m + radius
        flight_list = Box(cx + radius + COLUMN_GAP, top, width - m, bottom)
        return cls(
            width=width,
            height=height,
            margin=m,
            header_baseline=header_baseline,
            header_rule_y=header_rule_y,
            footer_rule_y=footer_rule_y,
            footer_baseline=footer_baseline,
            radar_cx=cx,
            radar_cy=cy,
            radar_radius=radius,
            flight_list=flight_list,
        )
