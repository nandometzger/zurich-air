"""Infer the runways in use from the traffic itself.

ADS-B says nothing about runways, but geometry does. An aircraft established on final
flies the extended centreline of its runway to within metres (seen at Zürich from 18 km
out), and a departure keeps the runway's track for a few kilometres beyond its end before
it turns. So every aircraft is compared with every runway *end*: "14" means the direction
from the 14 threshold towards the 32 threshold, whether landing or taking off.

An airborne aircraft is **landing on** an end when it is

* within ``max_cross_track_km`` of the extended centreline,
* between ``approach_max_km`` before the threshold and the far end of the runway,
* tracking within ``max_track_offset_deg`` of the runway direction,
* below a generous glide-path ceiling (``approach_margin_ft`` plus ``glide_ft_per_km``
  for every kilometre before the threshold), and
* not climbing (vertical rate below ``min_vertical_rate_fpm``, when reported);

it is **departing from** an end when it is aligned the same way, over the runway or up to
``departure_max_km`` beyond its far end, below ``departure_max_height_ft`` and climbing at
least ``min_vertical_rate_fpm`` (when reported). Over the runway itself only a clear
descent or climb decides, so that a take-off roll from a transponder that never reports
being on the ground is not mistaken for a landing. A single position only counts within
``tight_cross_track_km`` of the centreline; further out (up to ``max_cross_track_km``) the
previous position from the track history must be on the centreline too, behind it.

An aircraft **rolling** on the runway surface, aligned and faster than
``min_roll_speed_kt``, has just landed if it was airborne when last seen, and is taking off
if it was on the ground and slower.

Heights are barometric altitude minus the airport elevation; the margins absorb the
difference between pressure altitude and QNH. Helicopters, aircraft without altitude or
track, and stale positions never count. The inference is deliberately conservative: an
aircraft that is not clearly lined up with a runway is ignored, because a wrong runway on
the display is worse than none.

:class:`RunwayMonitor` remembers who did what for ``window_s`` (20 minutes): each aircraft
counts once per runway use, and the runways are ranked by how many aircraft used them. An
aircraft that climbs out along the runway after it was seen landing is going around, not
departing, and is not counted as a departure.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import is_helicopter
from zurich_air.processing.geo import angle_diff, bearing_deg, distance_km
from zurich_air.processing.history import Point, TrackHistory

Use = Literal["arrival", "departure"]


class RunwayLike(Protocol):
    """A runway with its two ends ("le" = low-numbered, "he" = high-numbered).

    Coordinates are the thresholds; the idents are the runway designators, e.g. "14"
    and "32".
    """

    @property
    def le_ident(self) -> str: ...
    @property
    def le_lat(self) -> float: ...
    @property
    def le_lon(self) -> float: ...
    @property
    def he_ident(self) -> str: ...
    @property
    def he_lat(self) -> float: ...
    @property
    def he_lon(self) -> float: ...


@dataclass(frozen=True)
class RunwayEnd:
    """One direction of a runway: from its threshold towards the opposite one."""

    ident: str
    lat: float  # threshold
    lon: float
    heading_deg: float  # true bearing towards the far threshold
    length_km: float

    def offsets_km(self, lat: float, lon: float) -> tuple[float, float]:
        """(along, cross) position of a point relative to this end, in km.

        *along* is positive past the threshold in the direction of travel; *cross* is
        positive to the right of the centreline.
        """
        d = distance_km(self.lat, self.lon, lat, lon)
        if d == 0.0:
            return 0.0, 0.0
        rel = math.radians(angle_diff(bearing_deg(self.lat, self.lon, lat, lon), self.heading_deg))
        return d * math.cos(rel), d * math.sin(rel)


def runway_ends(runways: Iterable[RunwayLike]) -> tuple[RunwayEnd, ...]:
    """Both directions of every runway."""
    ends = []
    for r in runways:
        ends.append(_end(r.le_ident, (r.le_lat, r.le_lon), (r.he_lat, r.he_lon)))
        ends.append(_end(r.he_ident, (r.he_lat, r.he_lon), (r.le_lat, r.le_lon)))
    return tuple(ends)


def _end(ident: str, threshold: Point, far: Point) -> RunwayEnd:
    heading = bearing_deg(*threshold, *far)
    return RunwayEnd(ident, *threshold, heading, distance_km(*threshold, *far))


@dataclass(frozen=True)
class RunwayTolerances:
    """How closely an aircraft must follow a runway to count; see the module docstring.

    The defaults suit an airliner airport like Zürich and data every 60 s.
    """

    max_cross_track_km: float = 0.3  # off the centreline, with a confirming earlier position
    tight_cross_track_km: float = 0.1  # off the centreline, from a single position
    max_track_offset_deg: float = 12.0  # track vs. runway direction (Zürich's differ by 18°)
    approach_max_km: float = 20.0  # landings: how far before the threshold
    approach_margin_ft: float = 1500.0  # landings: ceiling above the field at the threshold
    glide_ft_per_km: float = 200.0  # landings: plus this per km out (3° is 172 ft/km)
    departure_max_km: float = 5.0  # take-offs: how far beyond the runway end
    departure_max_height_ft: float = 5000.0  # take-offs: ceiling above the field
    min_vertical_rate_fpm: int = 300  # take-offs climb at least this; landings climb less
    min_roll_speed_kt: float = 40.0  # ground roll: faster than any taxiing aircraft
    runway_half_width_km: float = 0.1  # ground roll: off the centreline (generous)
    max_position_age_s: float = 30.0  # older positions are ignored


@dataclass(frozen=True)
class Sighting:
    """What an aircraft was doing when last seen, for telling take-off from landing roll."""

    at: datetime
    on_ground: bool
    ground_speed_kt: float | None


@dataclass(frozen=True)
class RunwayObservation:
    """One aircraft clearly using one runway end."""

    use: Use
    ident: str
    cross_track_km: float


@dataclass(frozen=True)
class RunwayUse:
    """Runway ends in use, most used first; empty when there is no evidence."""

    arrivals: tuple[str, ...] = ()
    departures: tuple[str, ...] = ()


def classify(
    aircraft: Aircraft,
    ends: Sequence[RunwayEnd],
    elevation_ft: float,
    tolerances: RunwayTolerances | None = None,
    *,
    previous_position: Point | None = None,
    previous_sighting: Sighting | None = None,
) -> RunwayObservation | None:
    """The runway end *aircraft* is clearly landing on or departing from, if any.

    *previous_position* is the aircraft's last earlier position (from the track history);
    *previous_sighting* its state at the last update, needed to classify a ground roll.
    """
    tol = tolerances or RunwayTolerances()
    a = aircraft
    if a.track_deg is None or a.position_age_s > tol.max_position_age_s or is_helicopter(a):
        return None
    if a.on_ground:
        found = (_ground_roll(a, end, tol, previous_sighting) for end in ends)
    else:
        found = (_airborne(a, end, elevation_ft, tol, previous_position) for end in ends)
    matches = [o for o in found if o is not None]
    return min(matches, key=lambda o: abs(o.cross_track_km), default=None)


def _aligned(track_deg: float, end: RunwayEnd, tol: RunwayTolerances) -> bool:
    return abs(angle_diff(track_deg, end.heading_deg)) <= tol.max_track_offset_deg


def _airborne(
    a: Aircraft,
    end: RunwayEnd,
    elevation_ft: float,
    tol: RunwayTolerances,
    previous: Point | None,
) -> RunwayObservation | None:
    if a.altitude_ft is None or a.track_deg is None or not _aligned(a.track_deg, end, tol):
        return None
    along, cross = end.offsets_km(a.lat, a.lon)
    if abs(cross) > tol.max_cross_track_km:
        return None
    if abs(cross) > tol.tight_cross_track_km and not _confirmed(previous, along, end, tol):
        return None
    use = _airborne_use(
        along, end.length_km, a.altitude_ft - elevation_ft, a.vertical_rate_fpm, tol
    )
    return None if use is None else RunwayObservation(use, end.ident, cross)


def _airborne_use(
    along: float, length_km: float, height_ft: float, rate: int | None, tol: RunwayTolerances
) -> Use | None:
    """Landing or take-off, from the position along the runway, height and vertical rate.

    Before the threshold only a landing fits and beyond the far end only a take-off, so a
    missing vertical rate is accepted there. Over the runway itself it takes a clear
    descent or climb: a take-off roll that is wrongly reported airborne flies level.
    """
    climbing = rate is not None and rate >= tol.min_vertical_rate_fpm
    descending = rate is not None and rate <= -tol.min_vertical_rate_fpm
    low_for_departure = height_ft <= tol.departure_max_height_ft
    if along < 0.0:  # before the threshold
        ceiling = tol.approach_margin_ft + tol.glide_ft_per_km * -along
        if along >= -tol.approach_max_km and height_ft <= ceiling and not climbing:
            return "arrival"
    elif along > length_km:  # beyond the far end
        beyond = along - length_km
        if beyond <= tol.departure_max_km and low_for_departure and (rate is None or climbing):
            return "departure"
    elif descending and height_ft <= tol.approach_margin_ft:
        return "arrival"
    elif climbing and low_for_departure:
        return "departure"
    return None


def _confirmed(previous: Point | None, along: float, end: RunwayEnd, tol: RunwayTolerances) -> bool:
    """The earlier position was on the centreline too, and behind the current one."""
    if previous is None:
        return False
    previous_along, previous_cross = end.offsets_km(*previous)
    return abs(previous_cross) <= tol.max_cross_track_km and previous_along < along


def _ground_roll(
    a: Aircraft, end: RunwayEnd, tol: RunwayTolerances, previous: Sighting | None
) -> RunwayObservation | None:
    speed = a.ground_speed_kt
    if previous is None or speed is None or speed < tol.min_roll_speed_kt:
        return None
    if a.track_deg is None or not _aligned(a.track_deg, end, tol):
        return None
    along, cross = end.offsets_km(a.lat, a.lon)
    if abs(cross) > tol.runway_half_width_km or not 0.0 <= along <= end.length_km:
        return None
    if not previous.on_ground:
        return RunwayObservation("arrival", end.ident, cross)  # touched down since
    if previous.ground_speed_kt is not None and speed > previous.ground_speed_kt:
        return RunwayObservation("departure", end.ident, cross)  # accelerating: take-off
    return None


@dataclass
class _Event:
    ident: str
    last_seen: datetime


class RunwayMonitor:
    """Remembers which aircraft used which runway end over the last ``window_s``.

    Call :meth:`update` with every snapshot's aircraft; it returns the runways in use.
    ``min_aircraft`` is how many different aircraft must have used an end within the
    window before it is reported. ``sighting_memory_s`` is how long an aircraft's last
    state is kept to tell a take-off roll from a landing roll.
    """

    def __init__(
        self,
        runways: Iterable[RunwayLike],
        elevation_ft: float,
        *,
        window_s: float = 1200.0,
        min_aircraft: int = 1,
        sighting_memory_s: float = 180.0,
        tolerances: RunwayTolerances | None = None,
    ) -> None:
        if window_s <= 0 or min_aircraft < 1:
            raise ValueError("window_s and min_aircraft must be positive")
        self.ends = runway_ends(runways)
        self.elevation_ft = elevation_ft
        self.window = timedelta(seconds=window_s)
        self.min_aircraft = min_aircraft
        self.sighting_memory = timedelta(seconds=sighting_memory_s)
        self.tolerances = tolerances or RunwayTolerances()
        self._events: dict[tuple[str, Use], _Event] = {}  # (icao, use) -> runway end
        self._sightings: dict[str, Sighting] = {}

    def update(
        self,
        aircraft: Iterable[Aircraft],
        now: datetime,
        history: TrackHistory | None = None,
    ) -> RunwayUse:
        """Classify *aircraft* seen at *now* (timezone-aware) and return the runways in use.

        With *history*, an aircraft slightly off the centreline still counts when its
        previous position confirms the alignment.
        """
        self.prune(now)
        seen: dict[str, Sighting] = {}
        for a in aircraft:
            trail = history.trail(a) if history is not None else ()
            observation = classify(
                a,
                self.ends,
                self.elevation_ft,
                self.tolerances,
                previous_position=trail[-1] if trail else None,
                previous_sighting=self._sightings.get(a.icao),
            )
            if observation is not None:
                self._record(a.icao, observation, now)
            seen[a.icao] = Sighting(now, a.on_ground, a.ground_speed_kt)
        self._sightings.update(seen)
        return self.use(now)

    def use(self, now: datetime) -> RunwayUse:
        """The runways in use according to what was seen within ``window_s`` of *now*."""
        cutoff = now - self.window
        return RunwayUse(
            arrivals=self._ranked("arrival", cutoff),
            departures=self._ranked("departure", cutoff),
        )

    def prune(self, now: datetime) -> None:
        """Forget observations older than the window and sightings older than their memory."""
        cutoff = now - self.window
        for key in [k for k, e in self._events.items() if e.last_seen < cutoff]:
            del self._events[key]
        cutoff = now - self.sighting_memory
        for icao in [i for i, s in self._sightings.items() if s.at < cutoff]:
            del self._sightings[icao]

    def _record(self, icao: str, observation: RunwayObservation, now: datetime) -> None:
        if observation.use == "departure" and (icao, "arrival") in self._events:
            return  # climbing out after an approach: a go-around, not a departure
        self._events[(icao, observation.use)] = _Event(observation.ident, now)

    def _ranked(self, use: Use, cutoff: datetime) -> tuple[str, ...]:
        """Runway ends by number of aircraft, then by most recent use."""
        counts: dict[str, int] = {}
        latest: dict[str, datetime] = {}
        for (_, kind), event in self._events.items():
            if kind != use or event.last_seen < cutoff:
                continue
            counts[event.ident] = counts.get(event.ident, 0) + 1
            latest[event.ident] = max(latest.get(event.ident, event.last_seen), event.last_seen)
        ranked = sorted(counts, key=lambda i: (-counts[i], -latest[i].timestamp(), i))
        return tuple(i for i in ranked if counts[i] >= self.min_aircraft)
