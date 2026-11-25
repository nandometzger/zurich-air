"""Choose which aircraft get a row in the flight list.

This is a deliberately simple, explainable placeholder; Milestone 4 will refine it.
Each aircraft gets a score from a few additive terms:

* proximity to the airport (0 at the edge of the radar, 1 at the airport),
* +1 if it is low (below 10 000 ft) and within 40 km of the airport — arriving or
  departing traffic, the aircraft you can actually see and hear from the city,
* +0.5 for a widebody,
* +100 for an emergency, which always comes first.

Aircraft on the ground are skipped unless ``aircraft.include_ground`` is set. Ties are
broken by ICAO address so the selection is deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import WIDEBODY_TYPES
from zurich_air.processing.filtering import is_airborne_or_wanted
from zurich_air.processing.geo import distance_km

TERMINAL_ALTITUDE_FT = 10_000
TERMINAL_RADIUS_KM = 40.0


def featured_score(aircraft: Aircraft, config: Config) -> float:
    """Interest score of one aircraft; higher is more interesting."""
    airport = config.airport
    dist = distance_km(airport.latitude, airport.longitude, aircraft.lat, aircraft.lon)
    score = max(0.0, 1.0 - dist / config.radar.range_km)
    alt = aircraft.altitude_ft
    if alt is not None and alt < TERMINAL_ALTITUDE_FT and dist < TERMINAL_RADIUS_KM:
        score += 1.0
    if aircraft.type_code and aircraft.type_code.upper() in WIDEBODY_TYPES:
        score += 0.5
    if aircraft.emergency:
        score += 100.0
    return score


def select_featured(
    aircraft: Iterable[Aircraft], config: Config, n: int | None = None
) -> list[Aircraft]:
    """The *n* most interesting aircraft (default ``aircraft.featured``), best first."""
    n = config.aircraft.featured if n is None else n
    candidates = [a for a in aircraft if is_airborne_or_wanted(a, config)]
    candidates.sort(key=lambda a: (-featured_score(a, config), a.icao))
    return candidates[:n]
