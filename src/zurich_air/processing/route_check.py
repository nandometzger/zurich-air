"""Show a route only when the aircraft is observed flying it.

A route table (``data/routes.py``) maps a callsign to a schedule, and schedules are wrong
often enough to matter: they go stale, airlines reuse a callsign for the return flight,
and crews key in the wrong one. Zurich Air never invents a destination, so a scheduled
route is accepted only when it agrees with the aircraft's own behaviour:

* **departure** — :func:`~zurich_air.processing.phase.flight_phase` says the aircraft is
  departing and the route has the local airport as a stop other than the last: the
  destination is the next stop.
* **arrival** — the phase is arrival and the local airport is a stop other than the
  first: the origin is the previous stop.
* **overflight** — the local airport is not on the route at all, and exactly one leg fits
  the geometry: the aircraft is heading for the leg's destination, has made progress
  from its origin, which lies behind it, without an implausible detour, and its altitude
  and vertical rate suit a flight between those two airports (see :func:`_leg_misfit`).

There is deliberately no cross-track limit in kilometres: airway routings stray far from
the great circle (real flights over Zürich were 200 to 290 km off it), so the checks ask
instead whether the aircraft is heading where the route says and has come from where the
route says. What cannot be checked from within radar range is whether it will really land
there; that residual risk is why departures and arrivals lean on the phase inference, and
why anything ambiguous gets no route at all.

Everything else returns ``None``: no route, no or a mismatching callsign, privacy-flagged
(LADD or PIA) aircraft, aircraft on the ground or with a stale position, a phase that the
route contradicts, the local airport on the route but no phase to confirm its role, and
multi-leg routes where more than one leg would fit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from zurich_air.data.aircraft import Aircraft
from zurich_air.data.routes import Route, normalise_callsign
from zurich_air.processing.geo import angle_diff, bearing_deg, distance_km
from zurich_air.processing.phase import Phase

Kind = Literal["departure", "arrival", "overflight"]
AirportPosition = Callable[[str], tuple[float, float] | None]  # ICAO code -> (lat, lon)


@dataclass(frozen=True, slots=True)
class VerifiedRoute:
    """The leg an aircraft is observed to fly, as ICAO airport codes."""

    origin: str
    destination: str
    kind: Kind


@dataclass(frozen=True, slots=True)
class _Limits:
    max_position_age_s: float
    max_track_offset_deg: float
    max_origin_offset_deg: float
    max_detour_ratio: float
    low_altitude_ft: int
    terminal_radius_km: float
    descent_rate_fpm: int
    max_descent_distance_km: float


@dataclass(frozen=True, slots=True)
class _Airport:
    icao: str
    position: tuple[float, float]


def verify_route(
    aircraft: Aircraft,
    route: Route | None,
    *,
    airport_icao: str,
    airport_lat: float,
    airport_lon: float,
    phase: Phase | None,
    airport_position: AirportPosition,
    max_position_age_s: float = 60.0,
    max_track_offset_deg: float = 30.0,
    max_origin_offset_deg: float = 60.0,
    max_detour_ratio: float = 1.10,
    low_altitude_ft: int = 10_000,
    terminal_radius_km: float = 150.0,
    descent_rate_fpm: int = 1000,
    max_descent_distance_km: float = 300.0,
    reasons: list[str] | None = None,
) -> VerifiedRoute | None:
    """The leg of *route* that *aircraft* is observed to fly, or ``None``.

    *airport_icao* and its position identify the local airport, *phase* is the result of
    :func:`~zurich_air.processing.phase.flight_phase` for this aircraft, and
    *airport_position* returns the position of any airport by ICAO code (``None`` if
    unknown). If *reasons* is given, a short explanation is appended to it whenever the
    result is ``None``, for ``zurich-air explain`` and the logs.

    Tunables (all but the first apply to overflights only):

    * *max_position_age_s* — older positions are not judged at all (the default matches
      ``aircraft.stale_after_s``).
    * *max_track_offset_deg* — the track must point within this angle of the initial
      great-circle bearing to the leg's destination. In live traffic over Zürich,
      verified flights were within 27 degrees.
    * *max_origin_offset_deg* — the leg's origin must lie within this angle of straight
      behind the aircraft (verified flights: up to 40 degrees). With the previous check
      this rejects a callsign reused for the return flight.
    * *max_detour_ratio* — the distance origin -> aircraft -> destination may exceed the
      direct one by this factor. Airliners fly only a few per cent farther than the great
      circle: verified flights were at most 1.06 (a departure 75 km out 1.08), while
      routes that were plainly wrong or doubtful had 1.14 to 1.49.
    * *low_altitude_ft*, *terminal_radius_km* — below this altitude an airliner is
      climbing out of or descending into an airport. That must be one of the leg's ends,
      within the radius, and nearer than the local airport (whose traffic the phase
      inference may simply not have recognised, on a downwind leg for example).
    * *descent_rate_fpm*, *max_descent_distance_km* — descending at least this fast
      farther than this from the destination means descending for somewhere else: the
      descent from cruise altitude starts about 200 km out.
    """
    limits = _Limits(
        max_position_age_s=max_position_age_s,
        max_track_offset_deg=max_track_offset_deg,
        max_origin_offset_deg=max_origin_offset_deg,
        max_detour_ratio=max_detour_ratio,
        low_altitude_ft=low_altitude_ft,
        terminal_radius_km=terminal_radius_km,
        descent_rate_fpm=descent_rate_fpm,
        max_descent_distance_km=max_descent_distance_km,
    )
    local = _Airport(airport_icao.strip().upper(), (airport_lat, airport_lon))
    result = _check(aircraft, route, local, phase, airport_position, limits)
    if isinstance(result, str):
        if reasons is not None:
            reasons.append(result)
        return None
    return result


def _check(
    aircraft: Aircraft,
    route: Route | None,
    local: _Airport,
    phase: Phase | None,
    airport_position: AirportPosition,
    limits: _Limits,
) -> VerifiedRoute | str:
    """The verified leg, or why there is none."""
    if route is None:
        return "no route"
    problem = _unobservable(aircraft, route, limits.max_position_age_s)
    if problem:
        return problem
    if local.icao in route.airports:
        return _local_leg(route, local.icao, phase)
    if phase is not None:
        return f"{phase} inferred but {local.icao} is not on the route"
    return _overflight_leg(aircraft, route, local, airport_position, limits)


def _unobservable(aircraft: Aircraft, route: Route, max_position_age_s: float) -> str | None:
    """Why the aircraft cannot be judged against *route* at all, if it cannot."""
    if aircraft.privacy:
        return "privacy-flagged"  # LADD or PIA: never show where it is going
    if aircraft.on_ground:
        return "on the ground"
    if aircraft.position_age_s > max_position_age_s:
        return "stale position"
    if not aircraft.callsign:
        return "no callsign"
    if normalise_callsign(aircraft.callsign) != route.callsign:
        return f"route is for {route.callsign}"
    return None


def _local_leg(route: Route, local: str, phase: Phase | None) -> VerifiedRoute | str:
    """The leg out of or into the local airport that the observed *phase* confirms."""
    if phase is None:
        return f"{local} on the route but no arrival or departure observed"
    if phase == "departure":
        legs = {(o, d) for o, d in route.legs if o == local and d != local}
    else:
        legs = {(o, d) for o, d in route.legs if d == local and o != local}
    if not legs:
        return f"{phase} inferred but the route has no such leg at {local}"
    if len(legs) > 1:
        return f"ambiguous: {len(legs)} legs at {local}"
    ((origin, destination),) = legs
    return VerifiedRoute(origin, destination, phase)


def _overflight_leg(
    aircraft: Aircraft,
    route: Route,
    local: _Airport,
    airport_position: AirportPosition,
    limits: _Limits,
) -> VerifiedRoute | str:
    """The single leg whose geometry fits a flight that does not involve the local airport."""
    fitting, misfits = set(), []
    for origin, destination in route.legs:
        misfit = _leg_misfit(aircraft, origin, destination, local, airport_position, limits)
        if misfit is None:
            fitting.add((origin, destination))
        else:
            misfits.append(f"{origin}-{destination}: {misfit}")
    if len(fitting) > 1:
        return f"ambiguous: {len(fitting)} legs fit"
    if not fitting:
        return "; ".join(misfits)
    ((origin, destination),) = fitting
    return VerifiedRoute(origin, destination, "overflight")


def _leg_misfit(
    aircraft: Aircraft,
    origin: str,
    destination: str,
    local: _Airport,
    airport_position: AirportPosition,
    limits: _Limits,
) -> str | None:
    """Why *aircraft* is not flying from *origin* to *destination*; ``None`` if it may be.

    Needs the track, the altitude and both ends' positions. The checks, in order:

    1. *Heading*: the track points at the destination (initial great-circle bearing).
    2. *Progress*: the aircraft is nearer the destination than the origin is.
    3. *Origin behind*: the bearing to the origin is roughly opposite the track.
    4. *Detour*: origin -> aircraft -> destination is not much longer than the leg.
    5. *Low altitude*: below ``low_altitude_ft`` the nearer end of the leg must be within
       ``terminal_radius_km`` and nearer than the local airport.
    6. *Early descent*: no fast descent farther than ``max_descent_distance_km`` from the
       destination.
    """
    a, track, altitude = aircraft, aircraft.track_deg, aircraft.altitude_ft
    if track is None or altitude is None:
        return "no track or altitude"
    if origin == destination:
        return "circular leg"
    start, end = airport_position(origin), airport_position(destination)
    if start is None or end is None:
        return f"unknown airport {origin if start is None else destination}"

    heading_off = abs(angle_diff(track, bearing_deg(a.lat, a.lon, *end)))
    if heading_off > limits.max_track_offset_deg:
        return f"track {heading_off:.0f} deg off {destination}"
    length = distance_km(*start, *end)
    to_start, to_end = distance_km(a.lat, a.lon, *start), distance_km(a.lat, a.lon, *end)
    if to_end >= length:
        return f"no nearer {destination} than {origin} is"
    origin_off = abs(angle_diff(bearing_deg(a.lat, a.lon, *start), track + 180.0))
    if origin_off > limits.max_origin_offset_deg:
        return f"{origin} {origin_off:.0f} deg from behind"
    detour = (to_start + to_end) / length
    if detour > limits.max_detour_ratio:
        return f"detour {detour:.2f} times the direct distance"
    if altitude < limits.low_altitude_ft:
        nearest = min(to_start, to_end)
        to_local = distance_km(a.lat, a.lon, *local.position)
        if nearest > limits.terminal_radius_km or to_local < nearest:
            return f"at {altitude} ft but not near {origin} or {destination}"
    rate = a.vertical_rate_fpm
    descending = rate is not None and rate <= -limits.descent_rate_fpm
    if descending and to_end > limits.max_descent_distance_km:
        return f"descending {to_end:.0f} km before {destination}"
    return None
