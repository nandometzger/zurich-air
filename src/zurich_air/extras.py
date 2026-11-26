"""What the display shows besides the traffic: the airport's weather and runways in use,
and verified routes for the flight list.

None of it is needed to draw a frame, so each part fails soft. METAR and route requests
never raise (their sources log a warning and back off, see data/metar.py and
data/routes.py), and an unexpected error in any part is logged as a warning once, until
that part works again, while the frame is drawn without it.

**Time per update.** The extras add at most one METAR request (``WEATHER_TIMEOUT_S``) and
``ROUTE_FETCHES_PER_UPDATE`` route file downloads (``ROUTE_TIMEOUT_S`` each) to an
update: at most ``MAX_EXTRA_S`` = 15 s, on top of the traffic sources' own timeouts. As
for those, a timeout bounds each blocking socket operation and DNS is not covered. A
lookup whose route file is not cached yet simply finds no route and succeeds on a later
update. The runway inference and route checks are local computations of a few
milliseconds.

**Routes** are looked up only for the aircraft in the flight list and those whose
arrival or departure is inferred (featured first, as the fetch budget is shared), never
for aircraft whose owners opted out of tracking (LADD/PIA). A route is shown only once
:func:`~zurich_air.processing.route_check.verify_route` accepts it, and it is remembered
for the aircraft (:class:`RouteMemory`): a departure verified within
``ranking.phase_radius_km`` keeps its destination as it flies on to the edge of the
radar, where the departure itself can no longer be told.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypeVar

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.airports import airport_position, lookup
from zurich_air.data.metar import MetarSource, Weather
from zurich_air.data.routes import Route, RouteTable
from zurich_air.processing.history import TrackHistory
from zurich_air.processing.phase import Phase, flight_phase
from zurich_air.processing.route_check import VerifiedRoute, verify_route
from zurich_air.processing.runways import RunwayMonitor, RunwayUse

log = logging.getLogger(__name__)
T = TypeVar("T")

WEATHER_TIMEOUT_S = 5.0  # per METAR request; at most one per update
ROUTE_TIMEOUT_S = 5.0  # per route file request
ROUTE_FETCHES_PER_UPDATE = 2
# The route table's budget period: shorter than an update interval, so that every update
# gets the full budget, and longer than the requests of one update can take, so that no
# update gets it twice.
ROUTE_BUDGET_PERIOD_S = 50.0
MAX_EXTRA_S = WEATHER_TIMEOUT_S + ROUTE_FETCHES_PER_UPDATE * ROUTE_TIMEOUT_S
ROUTE_MEMORY_S = 300.0  # as long as the track history remembers an aircraft

RouteLookup = Callable[[str | None], Route | None]  # callsign -> scheduled route


def check_route(
    aircraft: Aircraft,
    route: Route | None,
    config: Config,
    phase: Phase | None,
    reasons: list[str] | None = None,
) -> VerifiedRoute | None:
    """:func:`~zurich_air.processing.route_check.verify_route` with the configured airport.

    *phase* is :func:`~zurich_air.processing.phase.flight_phase` of *aircraft*. With
    *reasons*, why the route was rejected is appended to it.
    """
    airport = config.airport
    return verify_route(
        aircraft,
        route,
        airport_icao=airport.icao,
        airport_lat=airport.latitude,
        airport_lon=airport.longitude,
        phase=phase,
        airport_position=airport_position,
        max_position_age_s=config.aircraft.stale_after_s,
        reasons=reasons,
    )


def verified_routes(
    aircraft: Iterable[Aircraft], lookup_route: RouteLookup, config: Config
) -> dict[str, VerifiedRoute]:
    """The verified route of each of *aircraft* that has one, by ICAO address.

    Stateless: each aircraft is judged on its current position alone. The live app
    remembers routes instead (see :class:`Extras`).
    """
    routes = {}
    for a in aircraft:
        if a.privacy:
            continue
        route = check_route(a, lookup_route(a.callsign), config, flight_phase(a, config))
        if route is not None:
            routes[a.icao] = route
    return routes


@dataclass
class _Remembered:
    callsign: str
    route: VerifiedRoute
    last_seen: datetime


class RouteMemory:
    """Verified routes by aircraft, kept while it flies on under the same callsign.

    An entry is forgotten when its aircraft changes callsign or has not been seen for
    *forget_after_s*. Aircraft that opted out of tracking never get a route back.
    """

    def __init__(self, forget_after_s: float = ROUTE_MEMORY_S) -> None:
        self.forget_after = timedelta(seconds=forget_after_s)
        self._entries: dict[str, _Remembered] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def recall(self, aircraft: Aircraft) -> VerifiedRoute | None:
        """The route remembered for *aircraft* under its current callsign, if any."""
        entry = self._entries.get(aircraft.icao)
        if entry is None or aircraft.privacy or entry.callsign != _callsign(aircraft):
            return None
        return entry.route

    def remember(self, aircraft: Aircraft, route: VerifiedRoute, now: datetime) -> None:
        """Keep *route* for *aircraft* (seen at *now*) under its current callsign."""
        callsign = _callsign(aircraft)
        if callsign:
            self._entries[aircraft.icao] = _Remembered(callsign, route, now)

    def seen(self, aircraft: Iterable[Aircraft], now: datetime) -> None:
        """Note that *aircraft* were seen at *now*; forget those that changed callsign."""
        for a in aircraft:
            entry = self._entries.get(a.icao)
            if entry is None:
                continue
            if entry.callsign != _callsign(a):
                del self._entries[a.icao]
            else:
                entry.last_seen = max(entry.last_seen, now)

    def prune(self, now: datetime) -> None:
        """Forget aircraft not seen for ``forget_after_s``."""
        for icao in [i for i, e in self._entries.items() if now - e.last_seen > self.forget_after]:
            del self._entries[icao]


def _callsign(aircraft: Aircraft) -> str:
    return (aircraft.callsign or "").strip().upper()


def build_metar(config: Config) -> MetarSource | None:
    """The METAR source of ``[weather]``, or None if it is turned off."""
    weather = config.weather
    if not weather.enabled:
        return None
    return MetarSource(
        config.weather_station,
        interval_s=weather.interval_s,
        max_age_s=weather.max_age_s,
        timeout_s=WEATHER_TIMEOUT_S,
    )


def build_route_table(config: Config, *, fetch: bool = True) -> RouteTable | None:
    """The route table of ``[routes]``, or None if it is turned off.

    Without *fetch*, only routes already in the cache are found and nothing is downloaded.
    """
    if not config.routes.enabled:
        return None
    return RouteTable(
        Path(config.routes.cache_dir),
        fetch_budget=ROUTE_FETCHES_PER_UPDATE if fetch else 0,
        budget_period_s=ROUTE_BUDGET_PERIOD_S,
        timeout_s=ROUTE_TIMEOUT_S,
    )


def build_runway_monitor(config: Config) -> RunwayMonitor | None:
    """A monitor for the runways of ``airport.icao`` in the bundled airport data.

    None if ``airport.runways_in_use`` is off, or (with a warning in the log) if the
    bundled data has no runways for that airport.
    """
    airport = config.airport
    if not airport.runways_in_use:
        return None
    bundled = lookup(airport.icao)
    if bundled is None or not bundled.runways:
        why = "is not in the bundled airport data" if bundled is None else "has no runways there"
        log.warning("airport.icao %s %s: runways in use are not shown", airport.icao.upper(), why)
        return None
    return RunwayMonitor(bundled.runways, airport.elevation_ft)


class Extras:
    """The airport's weather and runways in use, and the routes for the flight list.

    Each source is optional: without it (turned off in the config, or the airport not
    in the bundled data) that part shows nothing. No method raises.
    """

    def __init__(
        self,
        config: Config,
        *,
        metar: MetarSource | None = None,
        route_table: RouteTable | None = None,
        runway_monitor: RunwayMonitor | None = None,
        memory: RouteMemory | None = None,
    ) -> None:
        self.config = config
        self.metar = metar
        self.route_table = route_table
        self.runway_monitor = runway_monitor
        self.memory = memory or RouteMemory()
        self._failing: dict[str, str] = {}  # part -> type of the error logged for it

    @classmethod
    def from_config(cls, config: Config) -> Extras:
        """Every part the config turns on, with the real network sources."""
        return cls(
            config,
            metar=build_metar(config),
            route_table=build_route_table(config),
            runway_monitor=build_runway_monitor(config),
        )

    def __str__(self) -> str:
        parts = []
        if self.metar is not None:
            parts.append(f"weather {self.metar.station} every {self.metar.interval_s:.0f} s")
        if self.runway_monitor is not None:
            parts.append(f"runways in use at {self.config.airport.icao.upper()}")
        if self.route_table is not None:
            parts.append(f"routes cached in {self.route_table.cache_dir}")
        return ", ".join(parts) or "none"

    def observe(
        self, aircraft: Sequence[Aircraft], history: TrackHistory | None, now: datetime
    ) -> None:
        """Watch newly arrived traffic for runway use: every aircraft reported, also those
        on the ground (*history* already holds their positions)."""
        monitor = self.runway_monitor
        if monitor is not None:
            self._guard("runway detection", None, lambda: monitor.update(aircraft, now, history))

    def weather(self, now: datetime) -> Weather | None:
        """The airport's latest observation if fresh enough, asking for a new one when due."""
        metar = self.metar
        if metar is None:
            return None
        return self._guard("weather", None, lambda: metar.weather(now))

    def runways(self, now: datetime) -> RunwayUse | None:
        """The runways seen in use within the monitor's window before *now*."""
        monitor = self.runway_monitor
        if monitor is None:
            return None
        return self._guard("runways in use", None, lambda: monitor.use(now))

    def routes(
        self,
        aircraft: Sequence[Aircraft],
        featured: Sequence[Aircraft],
        now: datetime,
        *,
        fresh: bool = True,
    ) -> dict[str, VerifiedRoute]:
        """Verified routes of the *featured* aircraft, by ICAO address.

        *aircraft* are all on the radar. With *fresh* data, the featured aircraft and those
        whose arrival or departure is inferred are checked, and their verified routes
        remembered; otherwise only remembered routes are returned.
        """
        if self.route_table is None:
            return {}
        return self._guard("routes", {}, lambda: self._routes(aircraft, featured, now, fresh))

    def _routes(
        self,
        aircraft: Sequence[Aircraft],
        featured: Sequence[Aircraft],
        now: datetime,
        fresh: bool,
    ) -> dict[str, VerifiedRoute]:
        assert self.route_table is not None
        memory = self.memory
        if fresh:
            for a, phase in self._route_candidates(aircraft, featured):
                if memory.recall(a) is None:
                    route = self.route_table.lookup(a.callsign)
                    verified = check_route(a, route, self.config, phase)
                    if verified is not None:
                        memory.remember(a, verified, now)
            memory.seen(aircraft, now)
            memory.prune(now)
        return {a.icao: route for a in featured if (route := memory.recall(a)) is not None}

    def _route_candidates(
        self, aircraft: Sequence[Aircraft], featured: Sequence[Aircraft]
    ) -> Iterator[tuple[Aircraft, Phase | None]]:
        """Featured aircraft first, then the others with an inferred phase."""
        listed = {a.icao for a in featured}
        for a in featured:
            if not a.privacy:
                yield a, flight_phase(a, self.config)
        for a in aircraft:
            if a.icao in listed or a.privacy:
                continue
            phase = flight_phase(a, self.config)
            if phase is not None:
                yield a, phase

    def _guard(self, part: str, fallback: T, fn: Callable[[], T]) -> T:
        """``fn()``, or *fallback* after an unexpected error, which is logged once per
        part and error type until the part works again."""
        try:
            result = fn()
        except Exception as exc:
            kind = type(exc).__name__
            if self._failing.get(part) != kind:
                self._failing[part] = kind
                log.warning(
                    "%s failed: %s: %s; shown without it until it works again",
                    part,
                    kind,
                    exc,
                    exc_info=exc,
                )
            return fallback
        if self._failing.pop(part, None) is not None:
            log.info("%s works again", part)
        return result
