"""Turn aircraft data into the short strings shown on the display.

Every function returns ``None`` (or an empty string) for missing data, so the layout can
simply omit the field — nothing is ever guessed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import operator_name, type_family, type_name
from zurich_air.processing.geo import M_PER_FT

TRANSITION_ALTITUDE_FT = 7000  # Swiss transition altitude: flight levels above this
LEVEL_THRESHOLD_FPM = 300  # vertical rates smaller than this count as level flight
SEPARATOR = " · "  # " · "

Trend = Literal["climb", "descend", "level"]


def altitude(
    altitude_ft: int | None, units: str = "aviation", *, on_ground: bool = False
) -> str | None:
    """``FL340`` / ``6500 FT`` (aviation) or ``10 360 M`` (metric); ``GND`` on the ground."""
    if on_ground:
        return "GND"
    if altitude_ft is None:
        return None
    if units == "metric":
        metres = round(altitude_ft * M_PER_FT, -1)
        return f"{int(metres):,} M".replace(",", " ")
    hundreds = round(altitude_ft / 100)
    if hundreds * 100 >= TRANSITION_ALTITUDE_FT:
        return f"FL{hundreds:03d}"
    return f"{hundreds * 100} FT"


def vertical_trend(rate_fpm: float | None) -> Trend | None:
    """Climb, descend or level, using a ±300 ft/min dead band."""
    if rate_fpm is None:
        return None
    if rate_fpm >= LEVEL_THRESHOLD_FPM:
        return "climb"
    if rate_fpm <= -LEVEL_THRESHOLD_FPM:
        return "descend"
    return "level"


def description(aircraft: Aircraft) -> str:
    """``Operator · Model`` from the reference tables; omits whatever is unknown.

    Falls back to the registration when neither is known, unless the label already shows
    it (private flights often use the registration without its hyphen as callsign).
    """
    return next(iter(descriptions(aircraft)), "")


def descriptions(aircraft: Aircraft) -> list[str]:
    """The description from most to least detailed, for the first one that fits.

    A long name is shortened by meaning, never cut off: ``airBaltic · A220-300`` →
    ``airBaltic · A220`` → ``A220-300`` → ``A220``. Empty when nothing is known.
    """
    operator = operator_name(aircraft.callsign)
    model = type_name(aircraft.type_code)
    family = type_family(aircraft.type_code) or model  # unknown codes have no family
    if operator and model:
        candidates = [f"{operator}{SEPARATOR}{model}", f"{operator}{SEPARATOR}{family}"]
        candidates += [model, family]
    else:
        candidates = [operator, model, family]
    result = list(dict.fromkeys(c for c in candidates if c))  # in order, without repeats
    registration = (aircraft.registration or "").strip()
    shown = registration.replace("-", "") == aircraft.label.replace("-", "")  # as the label
    if not result and registration and not shown:
        result = [registration]
    return result


def clock(moment: datetime, timezone: str) -> str:
    """``HH:MM`` local time of an aware datetime."""
    return moment.astimezone(ZoneInfo(timezone)).strftime("%H:%M")


def distance(km: float) -> str:
    """``80 KM``; one decimal only when needed."""
    return f"{km:g} KM" if km != int(km) else f"{int(km)} KM"
