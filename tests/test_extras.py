"""The extras of a frame: weather, runways in use and remembered, verified routes."""

import dataclasses
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zurich_air.config import AirportConfig, Config, RoutesConfig, WeatherConfig
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.metar import Weather
from zurich_air.data.routes import Route
from zurich_air.extras import (
    MAX_EXTRA_S,
    ROUTE_FETCHES_PER_UPDATE,
    ROUTE_MEMORY_S,
    WEATHER_TIMEOUT_S,
    Extras,
    RouteMemory,
    build_metar,
    build_route_table,
    build_runway_monitor,
    check_route,
    verified_routes,
)
from zurich_air.processing.geo import destination
from zurich_air.processing.phase import flight_phase
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.processing.runways import RunwayUse

CONFIG = Config()
T0 = datetime(2026, 12, 24, 14, 42, tzinfo=UTC)
AIRPORT = (CONFIG.airport.latitude, CONFIG.airport.longitude)
ROUTES = {
    "SWR8": Route("SWR8", ("LSZH", "KJFK")),
    "UAE87": Route("UAE87", ("OMDB", "LSZH")),
    "THY7HJ": Route("THY7HJ", ("EGLL", "LTFM")),
}
TO_JFK = VerifiedRoute("LSZH", "KJFK", "departure")


def departing(km_out=10.0, icao="4b0001", callsign="SWR8", **fields) -> Aircraft:
    """Climbing out westwards, *km_out* km from the airport."""
    lat, lon = destination(*AIRPORT, 270, km_out)
    values = {"altitude_ft": 3000 + int(km_out * 200), "track_deg": 270.0,
              "ground_speed_kt": 250.0, "vertical_rate_fpm": 2000} | fields  # fmt: skip
    return Aircraft(icao, lat, lon, callsign=callsign, **values)


class FakeTable:
    """Stands in for RouteTable: looks callsigns up in ROUTES and counts the lookups."""

    def __init__(self, routes=None, error: Exception | None = None) -> None:
        self.routes = ROUTES if routes is None else routes
        self.error = error
        self.looked_up: list[str | None] = []

    def lookup(self, callsign):
        self.looked_up.append(callsign)
        if self.error is not None:
            raise self.error
        return self.routes.get((callsign or "").strip())


class FakeMetar:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result, self.error, self.calls = result, error, 0

    def weather(self, now):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def extras(**kwargs) -> Extras:
    return Extras(CONFIG, **kwargs)


# --- route memory -------------------------------------------------------------------------


def test_a_departure_keeps_its_route_beyond_the_phase_radius():
    table = FakeTable()
    e = extras(route_table=table)
    near = departing(10)
    assert flight_phase(near, CONFIG) == "departure"
    assert e.routes([near], [near], T0) == {"4b0001": TO_JFK}

    far = departing(55)  # beyond ranking.phase_radius_km: no longer seen as departing
    assert flight_phase(far, CONFIG) is None
    assert check_route(far, ROUTES["SWR8"], CONFIG, None) is None  # on its own: rejected
    later = T0 + timedelta(minutes=8)
    assert e.routes([far], [far], later) == {"4b0001": TO_JFK}
    assert table.looked_up == ["SWR8"]  # remembered, not looked up again


def test_routes_are_remembered_for_aircraft_not_yet_in_the_list():
    e = extras(route_table=FakeTable())
    near = departing(10)
    assert e.routes([near], [], T0) == {}  # not featured, nothing shown, but checked
    far = departing(55)
    assert e.routes([far], [far], T0 + timedelta(minutes=8)) == {"4b0001": TO_JFK}


def test_a_new_callsign_forgets_the_route():
    e = extras(route_table=FakeTable())
    e.routes([departing(10)], [departing(10)], T0)
    renamed = departing(55, callsign="SWR9")
    assert e.routes([renamed], [renamed], T0 + timedelta(minutes=8)) == {}
    assert len(e.memory) == 0
    back = departing(56)  # and back again: it is no longer known to depart
    assert e.routes([back], [back], T0 + timedelta(minutes=9)) == {}


def test_an_aircraft_gone_for_a_while_is_forgotten():
    e = extras(route_table=FakeTable())
    near = departing(10)
    e.routes([near], [near], T0)
    other = departing(20, icao="4b0002", callsign=None)
    e.routes([other], [other], T0 + timedelta(seconds=ROUTE_MEMORY_S))
    assert len(e.memory) == 1  # not seen for exactly the memory time: still kept
    e.routes([other], [other], T0 + timedelta(seconds=ROUTE_MEMORY_S + 60))
    assert len(e.memory) == 0
    far = departing(55)
    assert e.routes([far], [far], T0 + timedelta(seconds=ROUTE_MEMORY_S + 120)) == {}


def test_stale_data_shows_remembered_routes_without_looking_anything_up():
    table = FakeTable()
    e = extras(route_table=table)
    near = departing(10)
    e.routes([near], [near], T0)
    later = T0 + timedelta(hours=2)  # not pruned: nothing new was seen
    assert e.routes([near], [near], later, fresh=False) == {"4b0001": TO_JFK}
    unknown = departing(12, icao="4b0009", callsign="UAE87")
    assert e.routes([unknown], [unknown], later, fresh=False) == {}
    assert table.looked_up == ["SWR8"]


def test_unverified_routes_are_never_shown():
    level = departing(20, vertical_rate_fpm=0)  # level flight: no departure inferred
    e = extras(route_table=FakeTable())
    assert e.routes([level], [level], T0) == {}
    wrong_way = departing(10, callsign="UAE87")  # the schedule says it arrives
    assert e.routes([wrong_way], [wrong_way], T0) == {}
    assert len(e.memory) == 0


def test_private_aircraft_are_never_looked_up():
    table = FakeTable()
    e = extras(route_table=table)
    private = departing(10, privacy=True)
    assert e.routes([private], [private], T0) == {}
    assert table.looked_up == []
    memory = RouteMemory()
    memory.remember(departing(10), TO_JFK, T0)
    assert memory.recall(private) is None


def test_featured_aircraft_are_looked_up_first():
    table = FakeTable()
    e = extras(route_table=table)
    others = [departing(5 + i, icao=f"4b00a{i}", callsign=f"EZY{i}") for i in range(3)]
    listed = departing(30, icao="4b00ff", callsign="SWR8")
    level = departing(25, icao="4b00ee", callsign="DLH1", vertical_rate_fpm=0)
    e.routes([*others, level, listed], [listed], T0)
    assert table.looked_up == ["SWR8", "EZY0", "EZY1", "EZY2"]  # level flight: not at all


def test_without_a_route_table_there_are_no_routes():
    near = departing(10)
    assert extras().routes([near], [near], T0) == {}


def test_verified_routes_judges_each_aircraft_alone():
    near, far = departing(10), departing(55, icao="4b0002")
    private = departing(10, icao="4b0003", privacy=True)
    routes = verified_routes([near, far, private], ROUTES.get, CONFIG)
    assert routes == {"4b0001": TO_JFK}


# --- fail soft ----------------------------------------------------------------------------


def test_route_errors_are_logged_once_and_show_no_route(caplog):
    table = FakeTable(error=RuntimeError("disk on fire"))
    e = extras(route_table=table)
    near = departing(10)
    with caplog.at_level(logging.INFO, logger="zurich_air.extras"):
        assert e.routes([near], [near], T0) == {}
        assert e.routes([near], [near], T0 + timedelta(minutes=1)) == {}
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "routes failed: RuntimeError: disk on fire" in warnings[0].getMessage()
        assert warnings[0].exc_info is not None  # with the traceback, once

        table.error = None
        assert e.routes([near], [near], T0 + timedelta(minutes=2)) == {"4b0001": TO_JFK}
        assert "routes works again" in caplog.text


def test_weather_errors_show_no_weather(caplog):
    metar = FakeMetar(error=ValueError("bad"))
    e = extras(metar=metar)
    with caplog.at_level(logging.WARNING, logger="zurich_air.extras"):
        assert e.weather(T0) is None
        assert e.weather(T0) is None
    assert metar.calls == 2
    assert caplog.text.count("weather failed") == 1


def test_runway_errors_show_no_runways():
    class Broken:
        def update(self, aircraft, now, history=None):
            raise KeyError("x")

        def use(self, now):
            raise KeyError("y")

    e = extras(runway_monitor=Broken())
    e.observe([departing(10)], None, T0)  # does not raise
    assert e.runways(T0) is None


def test_parts_that_are_off_show_nothing():
    e = extras()
    e.observe([departing(10)], None, T0)
    assert e.weather(T0) is None and e.runways(T0) is None
    assert str(e) == "none"


# --- building from the config -------------------------------------------------------------


def test_from_config_builds_every_part(tmp_path):
    config = dataclasses.replace(CONFIG, routes=RoutesConfig(cache_dir=str(tmp_path / "r")))
    e = Extras.from_config(config)
    assert e.metar is not None and e.metar.station == "LSZH"
    assert e.metar.timeout_s == WEATHER_TIMEOUT_S
    assert e.route_table is not None and e.route_table.cache_dir == tmp_path / "r"
    assert e.route_table.fetch_budget == ROUTE_FETCHES_PER_UPDATE
    assert e.runway_monitor is not None and len(e.runway_monitor.ends) == 6  # 3 runways
    assert str(e).startswith("weather LSZH every 600 s, runways in use at LSZH, routes")


def test_parts_can_be_turned_off():
    config = Config(
        airport=AirportConfig(runways_in_use=False),
        weather=WeatherConfig(enabled=False),
        routes=RoutesConfig(enabled=False),
    )
    assert build_metar(config) is None
    assert build_route_table(config) is None
    assert build_runway_monitor(config) is None


def test_weather_station_can_differ_from_the_airport():
    config = Config(weather=WeatherConfig(station="lsgg", interval_s=900))
    metar = build_metar(config)
    assert metar is not None and metar.station == "LSGG" and metar.interval_s == 900


def test_route_table_without_fetching_only_reads_the_cache():
    table = build_route_table(CONFIG, fetch=False)
    assert table is not None and table.fetch_budget == 0
    assert table.cache_dir == Path("cache/routes")


def test_unknown_airport_turns_runway_detection_off_with_a_log_line(caplog):
    config = Config(airport=AirportConfig(icao="ZZZZ"))
    with caplog.at_level(logging.WARNING, logger="zurich_air.extras"):
        assert build_runway_monitor(config) is None
    assert "airport.icao ZZZZ is not in the bundled airport data" in caplog.text
    assert "runways in use are not shown" in caplog.text


def test_extras_stay_well_within_an_update():
    assert MAX_EXTRA_S == 15.0
    assert CONFIG.update.interval_s / 4 >= MAX_EXTRA_S


@pytest.mark.parametrize("use", [RunwayUse(("14",), ("28",)), RunwayUse()])
def test_runways_in_use_come_from_the_monitor(use):
    class Monitor:
        def __init__(self) -> None:
            self.updates = []

        def update(self, aircraft, now, history=None):
            self.updates.append((tuple(aircraft), now, history))

        def use(self, now):
            return use

    monitor = Monitor()
    e = extras(runway_monitor=monitor)
    e.observe([departing(10)], None, T0)
    assert len(monitor.updates) == 1
    assert e.runways(T0) == use


def test_weather_comes_from_the_metar_source():
    weather = Weather("LSZH", T0, 270, False, 8, 18, 4, 1, 1018, "")
    assert extras(metar=FakeMetar(weather)).weather(T0) == weather
