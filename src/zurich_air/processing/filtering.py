"""Decide which reported aircraft belong on the display.

Pure functions over :class:`Aircraft`; the order of the input is preserved so the result
is deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing.geo import distance_km

# ADS-B emitter categories that are not aircraft: surface vehicles (C1, C2) and obstacles.
NOT_AIRCRAFT_CATEGORIES = frozenset({"C1", "C2", "C3", "C4", "C5"})


def dedupe(aircraft: Iterable[Aircraft]) -> list[Aircraft]:
    """One entry per ICAO address: the one with the freshest position, in first-seen order."""
    best: dict[str, Aircraft] = {}
    for a in aircraft:
        kept = best.get(a.icao)
        if kept is None or a.position_age_s < kept.position_age_s:
            best[a.icao] = a
    return list(best.values())


def is_aircraft(aircraft: Aircraft, config: Config) -> bool:
    """Airport vehicles and obstacles transmit ADS-B too; they never belong on the radar."""
    return aircraft.category not in NOT_AIRCRAFT_CATEGORIES


def is_airborne_or_wanted(aircraft: Aircraft, config: Config) -> bool:
    return config.aircraft.include_ground or not aircraft.on_ground


def is_fresh(aircraft: Aircraft, config: Config) -> bool:
    return aircraft.position_age_s <= config.aircraft.stale_after_s


def is_below_ceiling(aircraft: Aircraft, config: Config) -> bool:
    """Unknown altitudes pass: the aircraft is still there, we just don't know how high."""
    alt = aircraft.altitude_ft
    return alt is None or alt <= config.aircraft.max_altitude_ft


def is_in_range(aircraft: Aircraft, config: Config) -> bool:
    centre = config.location
    dist = distance_km(centre.latitude, centre.longitude, aircraft.lat, aircraft.lon)
    return dist <= config.radar.range_km


def visible_aircraft(aircraft: Iterable[Aircraft], config: Config) -> list[Aircraft]:
    """The aircraft to plot: deduplicated, no vehicles, airborne (unless ``include_ground``),
    with a recent position, below ``max_altitude_ft`` and inside the radar range."""
    checks = (is_aircraft, is_airborne_or_wanted, is_fresh, is_below_ceiling, is_in_range)
    return [a for a in dedupe(aircraft) if all(check(a, config) for check in checks)]
