"""Turn aircraft, weather and runway data into the short strings shown on the display.

Every function returns ``None`` (or an empty string or list) for missing data, so the
layout can simply omit the field — nothing is ever guessed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from zurich_air.data.aircraft import Aircraft
from zurich_air.data.airports import lookup
from zurich_air.data.metar import Weather
from zurich_air.data.reference import operator_name, type_family, type_name
from zurich_air.processing.geo import M_PER_FT
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.processing.runways import RunwayUse

TRANSITION_ALTITUDE_FT = 7000  # Swiss transition altitude: flight levels above this
LEVEL_THRESHOLD_FPM = 300  # vertical rates smaller than this count as level flight
SEPARATOR = " · "  # " · "
MINUS = "\u2212"  # U+2212 MINUS SIGN: longer than a hyphen, so it reads as a sign
ARROW = "\u2192"  # "→", in route labels

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


def altitudes(
    altitude_ft: int | None, units: str = "aviation", *, on_ground: bool = False
) -> list[str]:
    """The altitude from most to least complete, for the first one that fits.

    Only a long callsign beside it makes it give way: first the unit, then the space that
    groups the thousands (``6500 FT`` → ``6500``; ``10 360 M`` → ``10 360`` → ``10360``).
    The number itself never changes; flight levels and ``GND`` have nothing to give.
    Empty when the altitude is unknown.
    """
    full = altitude(altitude_ft, units, on_ground=on_ground)
    if full is None:
        return []
    number = full.rpartition(" ")[0]
    if not number:  # FL340, GND
        return [full]
    return list(dict.fromkeys([full, number, number.replace(" ", "")]))


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


# --- the airport line ---------------------------------------------------------------------


def wind(weather: Weather | None) -> str | None:
    """``270° 8 KT G18``, ``VRB 12 KT`` or ``CALM``, as reported in the METAR.

    The direction is in degrees true, with three digits as in the METAR; a wind whose
    direction is missing shows its speed alone.
    """
    if weather is None:
        return None
    if weather.calm:
        return "CALM"
    if weather.wind_variable:
        direction = "VRB "
    elif weather.wind_dir_deg is not None:
        direction = f"{weather.wind_dir_deg:03d}° "
    else:
        direction = ""
    text = f"{direction}{weather.wind_speed_kt} KT"
    return f"{text} G{weather.gust_kt}" if weather.gust_kt else text


def temperature(weather: Weather | None) -> str | None:
    """``4 °C``, ``0 °C``, ``-12 °C``, the latter with a real minus sign (:data:`MINUS`)."""
    if weather is None or weather.temperature_c is None:
        return None
    degrees = weather.temperature_c
    return f"{MINUS if degrees < 0 else ''}{abs(degrees)} °C"


def qnh(weather: Weather | None) -> str | None:
    """``QNH 1018``: the pressure in hPa that altimeters are set to near the airport."""
    if weather is None or weather.qnh_hpa is None:
        return None
    return f"QNH {weather.qnh_hpa}"


def runways(use: RunwayUse | None) -> list[str]:
    """``LDG 14 16`` and ``DEP 28 32``: landing and take-off runways, most used first."""
    if use is None:
        return []
    parts = []
    if use.arrivals:
        parts.append("LDG " + " ".join(use.arrivals))
    if use.departures:
        parts.append("DEP " + " ".join(use.departures))
    return parts


def airport_parts(weather: Weather | None, use: RunwayUse | None) -> list[str]:
    """The parts of the airport line, most important first: runways in use, wind,
    temperature and QNH. Whatever is unknown is left out."""
    measured = (wind(weather), temperature(weather), qnh(weather))
    return [*runways(use), *(part for part in measured if part)]


# --- routes -------------------------------------------------------------------------------


def airport_code(icao: str) -> str:
    """The code a passenger knows: IATA (``JFK``) if the airport has one, else ICAO."""
    airport = lookup(icao)
    return airport.display_code if airport is not None else icao.upper()


def route_label(route: VerifiedRoute) -> str:
    """``DXB →`` for an arrival, ``→ JFK`` for a departure, ``LHR → IST`` for an overflight.

    The arrow points from where the aircraft comes from to where it goes, as read, not
    the way it flies on the map.
    """
    if route.kind == "arrival":
        return f"{airport_code(route.origin)} {ARROW}"
    if route.kind == "departure":
        return f"{ARROW} {airport_code(route.destination)}"
    return f"{airport_code(route.origin)} {ARROW} {airport_code(route.destination)}"
