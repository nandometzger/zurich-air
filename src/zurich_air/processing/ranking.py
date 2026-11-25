"""Choose which aircraft get a row in the flight list, and say why.

Each candidate is scored by small, independent components. A component looks at one
aspect (flight phase, type, altitude, …) and returns a :class:`Reason` — a short text and
the points it adds — or ``None``. The score is the sum of the points, so every ranking can
be explained line by line (``zurich-air explain``). Points come from ``[ranking.weights]``
in the config, thresholds from ``[ranking]``.

Rules on top of the points:

* an emergency (squawk 7500/7600/7700 or emergency status) always comes first;
* aircraft whose owner opted out of public tracking (LADD) or that use a privacy address
  (PIA) are never featured — they remain anonymous markers on the radar;
* aircraft on the ground are skipped unless ``aircraft.include_ground`` is set;
* aircraft featured in the previous frame get the ``featured_before`` bonus, so the list
  only changes when a newcomer is clearly more interesting — a calm e-paper;
* rows stay in place: aircraft that remain featured keep their previous order and each
  newcomer is slotted in above the first row it outscores, so rows never swap places and
  only move up when one above leaves;
* ties are broken by ICAO address, so the result is deterministic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.readsb import EMERGENCY_SQUAWKS
from zurich_air.data.reference import NOTABLE_TYPES, WIDEBODY_TYPES, is_helicopter, is_rescue
from zurich_air.processing.filtering import is_airborne_or_wanted
from zurich_air.processing.geo import distance_km
from zurich_air.processing.phase import Phase, flight_phase

EMERGENCY_POINTS = 1000.0  # far above any sum of weights (each is at most 100)
NO_ALTITUDE_POINTS_FT = 40_000  # the low-altitude bonus fades out towards this altitude


@dataclass(frozen=True)
class Reason:
    """One line of explanation: what was noticed and how many points it is worth."""

    text: str
    points: float

    def __str__(self) -> str:
        return f"{self.text} {self.points:+.0f}"


@dataclass(frozen=True)
class Facts:
    """An aircraft plus the derived values the components look at."""

    aircraft: Aircraft
    phase: Phase | None
    airport_km: float  # distance to the airport reference point
    centre_km: float  # distance to the radar centre
    featured_before: bool


@dataclass(frozen=True)
class Candidate:
    """A ranked aircraft: its score, the reasons behind it and its flight phase."""

    aircraft: Aircraft
    score: float
    reasons: tuple[Reason, ...]
    phase: Phase | None
    airport_km: float

    @property
    def sort_key(self) -> tuple[bool, float, str]:
        """Emergencies first, then highest score, then ICAO address."""
        return (not self.aircraft.emergency, -self.score, self.aircraft.icao)


# --- components ---------------------------------------------------------------------------

Component = Callable[[Facts, Config], Reason | None]


def emergency(f: Facts, config: Config) -> Reason | None:
    if not f.aircraft.emergency:
        return None
    squawk = f.aircraft.squawk
    text = f"emergency {squawk}" if squawk in EMERGENCY_SQUAWKS else "emergency"
    return Reason(text, EMERGENCY_POINTS)


def phase(f: Facts, config: Config) -> Reason | None:
    """Arrivals and departures, worth more the closer they are to the runways."""
    if f.phase is None:
        return None
    weight = getattr(config.ranking.weights, f.phase)
    closeness = 1.0 - 0.5 * min(1.0, f.airport_km / config.ranking.phase_radius_km)
    return Reason(f"{f.phase} {config.airport.code} {f.airport_km:.0f} km", weight * closeness)


def notable_type(f: Facts, config: Config) -> Reason | None:
    code = (f.aircraft.type_code or "").upper()
    weights = config.ranking.weights
    if code in NOTABLE_TYPES:
        return Reason(f"notable {code}", weights.notable_type)
    if code in WIDEBODY_TYPES:
        return Reason(f"widebody {code}", weights.widebody)
    return None


def helicopter(f: Facts, config: Config) -> Reason | None:
    if not is_helicopter(f.aircraft):
        return None
    return Reason("helicopter", config.ranking.weights.helicopter)


def rescue(f: Facts, config: Config) -> Reason | None:
    if not is_rescue(f.aircraft):
        return None
    return Reason("air rescue", config.ranking.weights.rescue)


def military(f: Facts, config: Config) -> Reason | None:
    if not f.aircraft.military:
        return None
    return Reason("military", config.ranking.weights.military)


def interesting(f: Facts, config: Config) -> Reason | None:
    if not f.aircraft.interesting:
        return None
    return Reason("flagged interesting", config.ranking.weights.interesting)


def low_altitude(f: Facts, config: Config) -> Reason | None:
    """Lower is more visible and audible: full points at 0 ft, none from 40 000 ft."""
    altitude = f.aircraft.altitude_ft
    if altitude is None:
        return None
    lowness = min(1.0, max(0.0, 1.0 - altitude / NO_ALTITUDE_POINTS_FT))
    return Reason(f"{altitude} ft", config.ranking.weights.low_altitude * lowness)


def near_centre(f: Facts, config: Config) -> Reason | None:
    nearness = max(0.0, 1.0 - f.centre_km / config.radar.range_km)
    return Reason(
        f"{f.centre_km:.0f} km from centre", config.ranking.weights.near_centre * nearness
    )


def no_identity(f: Facts, config: Config) -> Reason | None:
    """A row without callsign and type says little; prefer aircraft we can describe."""
    if f.aircraft.callsign or f.aircraft.type_code:
        return None
    return Reason("no callsign or type", config.ranking.weights.no_identity)


def featured_before(f: Facts, config: Config) -> Reason | None:
    if not f.featured_before:
        return None
    return Reason("featured before", config.ranking.weights.featured_before)


COMPONENTS: tuple[Component, ...] = (
    emergency,
    phase,
    notable_type,
    helicopter,
    rescue,
    military,
    interesting,
    low_altitude,
    near_centre,
    no_identity,
    featured_before,
)


# --- ranking ------------------------------------------------------------------------------


def is_candidate(aircraft: Aircraft, config: Config) -> bool:
    """May this aircraft be featured at all? Never if its owner asked for privacy."""
    return not aircraft.privacy and is_airborne_or_wanted(aircraft, config)


def facts(aircraft: Aircraft, config: Config, featured_before: bool = False) -> Facts:
    airport, centre = config.airport, config.location
    return Facts(
        aircraft=aircraft,
        phase=flight_phase(aircraft, config),
        airport_km=distance_km(airport.latitude, airport.longitude, aircraft.lat, aircraft.lon),
        centre_km=distance_km(centre.latitude, centre.longitude, aircraft.lat, aircraft.lon),
        featured_before=featured_before,
    )


def evaluate(aircraft: Aircraft, config: Config, featured_before: bool = False) -> Candidate:
    """Score one aircraft with every component."""
    f = facts(aircraft, config, featured_before)
    reasons = tuple(
        r for component in COMPONENTS if (r := component(f, config)) is not None and r.points
    )
    return Candidate(
        aircraft=aircraft,
        score=sum(r.points for r in reasons),
        reasons=reasons,
        phase=f.phase,
        airport_km=f.airport_km,
    )


def rank(
    aircraft: Iterable[Aircraft],
    config: Config,
    previous: Sequence[str] = (),
    n: int | None = None,
) -> list[Candidate]:
    """Every candidate: the *n* featured ones (default ``aircraft.featured``) in display
    order, then the rest by score. *previous* lists the ICAO addresses featured last
    time, in display order."""
    n = config.aircraft.featured if n is None else n
    before = set(previous)
    candidates = sorted(
        (
            evaluate(a, config, featured_before=a.icao in before)
            for a in aircraft
            if is_candidate(a, config)
        ),
        key=lambda c: c.sort_key,
    )
    return keep_rows(candidates[:n], previous) + candidates[n:]


def keep_rows(featured: Sequence[Candidate], previous: Sequence[str]) -> list[Candidate]:
    """Display order of *featured* (best first): previous rows keep their order, each
    newcomer goes above the first row it outscores, and emergencies go to the top."""
    position = {icao: i for i, icao in enumerate(previous)}
    rows = sorted(
        (c for c in featured if c.aircraft.icao in position),
        key=lambda c: position[c.aircraft.icao],
    )
    for newcomer in (c for c in featured if c.aircraft.icao not in position):
        below = (i for i, row in enumerate(rows) if newcomer.sort_key < row.sort_key)
        rows.insert(next(below, len(rows)), newcomer)
    return sorted(rows, key=lambda c: not c.aircraft.emergency)  # stable: order kept


def select_featured(
    aircraft: Iterable[Aircraft],
    config: Config,
    n: int | None = None,
    previous: Sequence[str] = (),
) -> list[Aircraft]:
    """The *n* most interesting aircraft (default ``aircraft.featured``) in display order."""
    n = config.aircraft.featured if n is None else n
    return [c.aircraft for c in rank(aircraft, config, previous, n)[:n]]
