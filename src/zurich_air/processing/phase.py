"""Infer whether an aircraft is arriving at or departing from the airport.

ADS-B carries no flight plan, so the phase is read from the kinematics alone:

* **arrival** — below ``ranking.arrival_max_altitude_ft``, descending (or level and very
  low), with its track pointing at the airport;
* **departure** — below ``ranking.departure_max_altitude_ft``, climbing, with its track
  pointing away from the airport;

both within ``ranking.phase_radius_km`` of the airport. "Pointing at" allows
``ranking.max_track_offset_deg`` between the track and the direct line.

The inference is deliberately conservative: an aircraft on a downwind leg, missing data,
a helicopter or anything else ambiguous gets no phase (``None``) rather than a wrong one.
"""

from __future__ import annotations

from typing import Literal

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import is_helicopter
from zurich_air.processing.filtering import is_on_ground
from zurich_air.processing.geo import angle_diff, bearing_deg, distance_km

Phase = Literal["arrival", "departure"]

# Level flight this close above the field still counts as arriving (flare, odd rates).
LOW_ABOVE_FIELD_FT = 2000


def flight_phase(aircraft: Aircraft, config: Config) -> Phase | None:
    """``"arrival"``, ``"departure"`` or ``None`` when the data does not say clearly."""
    a, airport, limits = aircraft, config.airport, config.ranking
    altitude, rate, track = a.altitude_ft, a.vertical_rate_fpm, a.track_deg
    if altitude is None or rate is None or track is None:
        return None
    if is_on_ground(a, config) or is_helicopter(a):
        return None  # helicopters fly between pads, not along the runways
    if distance_km(airport.latitude, airport.longitude, a.lat, a.lon) > limits.phase_radius_km:
        return None

    to_airport = bearing_deg(a.lat, a.lon, airport.latitude, airport.longitude)
    offset = abs(angle_diff(track, to_airport))  # 0: straight at the airport, 180: away
    min_rate = limits.min_vertical_rate_fpm
    descending = rate <= -min_rate
    very_low = abs(rate) < min_rate and altitude <= airport.elevation_ft + LOW_ABOVE_FIELD_FT
    if (
        offset <= limits.max_track_offset_deg
        and altitude <= limits.arrival_max_altitude_ft
        and (descending or very_low)
    ):
        return "arrival"
    if (
        180.0 - offset <= limits.max_track_offset_deg
        and altitude <= limits.departure_max_altitude_ft
        and rate >= min_rate
    ):
        return "departure"
    return None
