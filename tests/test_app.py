import dataclasses
import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from zurich_air.app import (
    CLOCK_RETRY_S,
    MAX_DISPLAY_FAILURES,
    WATCHDOG_PING_S,
    App,
    ErrorLog,
    HourlySummary,
    TickReport,
)
from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft, TrafficSnapshot
from zurich_air.data.metar import Weather
from zurich_air.data.provider import ProviderError
from zurich_air.data.routes import Route
from zurich_air.extras import Extras
from zurich_air.processing.geo import destination
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.processing.runways import RunwayUse
from zurich_air.rendering.renderer import Renderer

CONFIG = Config()
T0 = datetime(2026, 12, 24, 14, 42, 1, tzinfo=UTC)
LAT, LON = CONFIG.location.latitude, CONFIG.location.longitude
ZRH = ZoneInfo("Europe/Zurich")


def snapshot(at=T0, lat_offset=0.1):
    aircraft = Aircraft(icao="4b0001", lat=LAT + lat_offset, lon=LON, callsign="SWR001")
    return TrafficSnapshot(aircraft=(aircraft,), timestamp=at, source="adsb.fi")


class FakeProvider:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def fetch(self, lat, lon, radius_km):
        self.calls.append((lat, lon, radius_km))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MemoryDisplay:
    def __init__(self, *failures) -> None:
        self.frames = []
        self.failures = list(failures)  # True: that call to show() raises
        self.closed = False

    def show(self, image):
        if self.failures and self.failures.pop(0):
            raise OSError("SPI write failed")
        self.frames.append(image.copy())

    def close(self):
        self.closed = True


class FakeNotifier:
    def __init__(self) -> None:
        self.sent = []

    def ready(self):
        self.sent.append("READY=1")

    def watchdog(self):
        self.sent.append("WATCHDOG=1")

    def stopping(self):
        self.sent.append("STOPPING=1")


class FakeTime:
    """A wall clock that only moves when the loop sleeps (see InstantStop)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class InstantStop(threading.Event):
    """A stop event whose waits return at once, moving *clock* on by the time asked for."""

    def __init__(self, clock: FakeTime | None = None) -> None:
        super().__init__()
        self.clock = clock
        self.waits = []

    def wait(self, timeout=None):
        if self.is_set():
            return True
        assert len(self.waits) < 1000, "the loop never stopped"
        self.waits.append(timeout)
        if self.clock:
            self.clock.now += timedelta(seconds=timeout)
        return False


def stop_after(calls, stop, outcome=None):
    """A provider that sets *stop* during its *calls*-th fetch."""
    seen = []

    class Provider:
        def fetch(self, lat, lon, radius_km):
            seen.append(1)
            if len(seen) >= calls:
                stop.set()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome or snapshot()

    return Provider()


def test_tick_fetches_renders_and_shows():
    provider = FakeProvider(snapshot())
    display = MemoryDisplay()
    app = App(CONFIG, provider, display)
    report = app.tick(T0)
    assert report.fresh and report.refreshed and report.status == "live"
    assert len(display.frames) == 1
    assert display.frames[0].size == (400, 300)
    lat, lon, radius = provider.calls[0]
    assert (lat, lon) == (LAT, LON)
    assert radius > CONFIG.radar.range_km
    assert app.last_success == T0


def test_unchanged_frame_is_not_shown_again():
    data = snapshot()
    display = MemoryDisplay()
    app = App(CONFIG, FakeProvider(data, data), display)
    app.tick(T0)
    report = app.tick(T0 + timedelta(seconds=30))  # same minute on the clock, same traffic
    assert len(display.frames) == 1
    assert not report.refreshed


def test_changed_traffic_is_shown():
    display = MemoryDisplay()
    later = T0 + timedelta(seconds=20)
    app = App(CONFIG, FakeProvider(snapshot(), snapshot(later, 0.2)), display)
    app.tick(T0)
    app.tick(later)
    assert len(display.frames) == 2


def test_failure_keeps_last_data_then_turns_stale():
    data = snapshot()
    display = MemoryDisplay()
    failures = [ProviderError("down")] * 3
    app = App(CONFIG, FakeProvider(data, *failures), display)
    app.tick(T0)
    report = app.tick(T0 + timedelta(seconds=60))
    assert not report.fresh and report.problem == "down"
    assert app.snapshot is data
    assert app.last_success == T0

    stale_at = T0 + timedelta(seconds=CONFIG.update.stale_after_s + 1)
    assert app.tick(stale_at).status == "stale"
    assert app.snapshot is data
    # The stale frame keeps the aircraft but differs from the live one (indicator, footer).
    assert display.frames[-1].tobytes() != display.frames[0].tobytes()


def test_long_outage_clears_the_frozen_aircraft():
    display = MemoryDisplay()
    app = App(CONFIG, FakeProvider(snapshot(), *[ProviderError("down")] * 2), display)
    app.tick(T0)
    stale = app.tick(T0 + timedelta(seconds=CONFIG.update.hide_after_s))
    assert stale.status == "stale" and stale.aircraft == 1 and stale.featured == ("SWR001",)

    hidden = app.tick(T0 + timedelta(seconds=CONFIG.update.hide_after_s + 60))
    assert hidden.status == "waiting"
    assert hidden.aircraft == 0 and hidden.featured == ()
    assert hidden.refreshed
    assert app.featured == ()


def test_failure_without_any_data_shows_waiting_frame():
    display = MemoryDisplay()
    app = App(CONFIG, FakeProvider(ProviderError("down")), display)
    report = app.tick(T0)
    assert not report.fresh
    assert report.status == "waiting"
    assert app.snapshot is None
    assert len(display.frames) == 1


@pytest.mark.parametrize("failure", [ProviderError("no data"), OSError("network down")])
def test_start_draws_the_status_screen_before_the_first_wait(failure):
    # Replaces whatever the panel kept from before a shutdown within the first tick.
    stop = InstantStop(FakeTime(T0))
    shown_after_waits = []

    class Display(MemoryDisplay):
        def show(self, image):
            shown_after_waits.append(len(stop.waits))
            super().show(image)

    app = App(CONFIG, stop_after(1, stop, failure), Display(), clock=stop.clock)
    app.run(stop, handle_signals=False)
    assert shown_after_waits == [0]


def test_history_builds_trails_across_ticks():
    app = App(
        CONFIG, FakeProvider(snapshot(), snapshot(T0 + timedelta(minutes=1), 0.2)), MemoryDisplay()
    )
    app.tick(T0)
    app.tick(T0 + timedelta(minutes=1))
    assert app.history.trail(app.snapshot.aircraft[0]) == ((LAT + 0.1, LON),)


def test_featured_list_is_carried_between_ticks():
    config = dataclasses.replace(CONFIG, aircraft=dataclasses.replace(CONFIG.aircraft, featured=1))
    airport = (config.airport.latitude, config.airport.longitude)

    def arrival(icao, dist_km):
        lat, lon = destination(*airport, 317, dist_km)
        return Aircraft(
            icao=icao, lat=lat, lon=lon, callsign="SWR1", altitude_ft=1500 + dist_km * 320,
            track_deg=137, vertical_rate_fpm=-800,
        )  # fmt: skip

    first = TrafficSnapshot(aircraft=(arrival("a", 20),), timestamp=T0, source="adsb.fi")
    later = T0 + timedelta(minutes=1)
    second = TrafficSnapshot(  # "b" is a little more interesting, but not clearly
        aircraft=(arrival("a", 20), arrival("b", 15)), timestamp=later, source="adsb.fi"
    )
    app = App(config, FakeProvider(first, second), MemoryDisplay())
    app.tick(T0)
    assert app.featured == ("a",)
    app.tick(later)
    assert app.featured == ("a",)

    fresh = App(config, FakeProvider(second), MemoryDisplay())
    fresh.tick(later)
    assert fresh.featured == ("b",)


# Weather, runways in use and routes ---------------------------------------------------

WEATHER = Weather("LSZH", T0 - timedelta(minutes=22), 270, False, 8, 18, 4, 1, 1018, "")


def departing_swr8(at=T0, km_out=10.0):
    """SWR8 climbing out westwards, *km_out* km from the airport."""
    airport = (CONFIG.airport.latitude, CONFIG.airport.longitude)
    lat, lon = destination(*airport, 270, km_out)
    aircraft = Aircraft(
        "4b0008", lat, lon, callsign="SWR8", type_code="B77W", altitude_ft=5000,
        track_deg=270, ground_speed_kt=250, vertical_rate_fpm=2000,
    )  # fmt: skip
    return TrafficSnapshot(aircraft=(aircraft,), timestamp=at, source="adsb.fi")


class FakeMetar:
    def __init__(self, error=None) -> None:
        self.error = error

    def weather(self, now):
        if self.error:
            raise self.error
        return WEATHER


class FakeRouteTable:
    def __init__(self, error=None) -> None:
        self.error = error

    def lookup(self, callsign):
        if self.error:
            raise self.error
        return Route("SWR8", ("LSZH", "KJFK")) if callsign == "SWR8" else None


class FakeMonitor:
    def __init__(self, error=None) -> None:
        self.error = error
        self.updates = []

    def update(self, aircraft, now, history=None):
        if self.error:
            raise self.error
        self.updates.append(now)

    def use(self, now):
        if self.error:
            raise self.error
        return RunwayUse(("14",), ("28",)) if self.updates else RunwayUse()


class SceneRecorder(Renderer):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.scenes = []

    def render(self, scene):
        self.scenes.append(scene)
        return super().render(scene)


def app_with_extras(provider, error=None):
    extras = Extras(
        CONFIG,
        metar=FakeMetar(error),
        route_table=FakeRouteTable(error),
        runway_monitor=FakeMonitor(error),
    )
    renderer = SceneRecorder(CONFIG)
    return App(CONFIG, provider, MemoryDisplay(), renderer=renderer, extras=extras), renderer


def test_tick_shows_weather_runways_and_verified_routes():
    app, renderer = app_with_extras(FakeProvider(departing_swr8()))
    report = app.tick(T0)
    assert report.refreshed and not report.errors
    scene = renderer.scenes[-1]
    assert scene.weather == WEATHER
    assert scene.runways == RunwayUse(("14",), ("28",))
    assert scene.routes == {"4b0008": VerifiedRoute("LSZH", "KJFK", "departure")}


def test_runways_are_inferred_from_new_data_only():
    data = departing_swr8()
    app, _ = app_with_extras(FakeProvider(data, ProviderError("down"), data))
    monitor = app.extras.runway_monitor
    app.tick(T0)
    app.tick(T0 + timedelta(minutes=1))  # no new data: the old positions are not counted again
    app.tick(T0 + timedelta(minutes=2))
    assert monitor.updates == [T0, T0 + timedelta(minutes=2)]


def test_a_departure_keeps_its_route_as_it_flies_out():
    later = T0 + timedelta(minutes=8)
    app, renderer = app_with_extras(FakeProvider(departing_swr8(), departing_swr8(later, 55)))
    app.tick(T0)
    app.tick(later)
    assert renderer.scenes[-1].routes == {"4b0008": VerifiedRoute("LSZH", "KJFK", "departure")}


def test_weather_stays_while_waiting_for_traffic():
    app, renderer = app_with_extras(FakeProvider(ProviderError("down")))
    report = app.tick(T0)
    assert report.status == "waiting"
    assert renderer.scenes[-1].weather == WEATHER
    assert renderer.scenes[-1].routes == {}


@pytest.mark.parametrize("error", [RuntimeError("bug"), OSError("disk full"), KeyError("x")])
def test_failing_extras_never_break_a_tick(error, caplog):
    app, renderer = app_with_extras(FakeProvider(departing_swr8(), departing_swr8()), error)
    with caplog.at_level(logging.WARNING):
        first = app.tick(T0)
        second = app.tick(T0 + timedelta(minutes=1))
    assert first.refreshed and first.fresh and not first.errors
    assert second.fresh and not second.errors
    scene = renderer.scenes[-1]
    assert scene.weather is None and scene.runways is None and scene.routes == {}
    assert scene.featured  # the traffic is shown all the same
    extras_warnings = [r for r in caplog.records if r.name == "zurich_air.extras"]
    assert len(extras_warnings) == 4  # once per part: detection, weather, runways, routes


# Unexpected errors -------------------------------------------------------------------


def test_unexpected_fetch_error_still_updates_the_display(caplog):
    display = MemoryDisplay()
    app = App(CONFIG, FakeProvider(snapshot(), KeyError("lat"), KeyError("lat")), display)
    app.tick(T0)
    late = T0 + timedelta(seconds=CONFIG.update.stale_after_s + 1)
    with caplog.at_level(logging.ERROR, logger="zurich_air.app"):
        report = app.tick(late)
        app.tick(late + timedelta(minutes=1))
    assert report.errors == ("fetch",) and not report.fresh
    assert report.status == "stale" and report.refreshed  # the screen still tells the truth
    failures = [r for r in caplog.records if r.message.startswith("fetch failed")]
    assert len(failures) == 2
    assert failures[0].exc_info is not None  # the traceback, once
    assert failures[1].exc_info is None and "again" in failures[1].message


def test_render_error_is_survived():
    class FlakyRenderer:
        def __init__(self) -> None:
            self.calls = 0

        def render(self, scene):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("bad glyph")
            return Renderer(CONFIG).render(scene)

    display = MemoryDisplay()
    app = App(CONFIG, FakeProvider(snapshot(), snapshot()), display, renderer=FlakyRenderer())
    assert app.tick(T0).errors == ("render",)
    assert display.frames == []
    report = app.tick(T0 + timedelta(minutes=1))
    assert report.errors == () and report.refreshed


def test_build_error_is_survived(monkeypatch):
    import zurich_air.app as app_module

    def broken(*args):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(app_module, "build_scene", broken)
    display = MemoryDisplay()
    report = App(CONFIG, FakeProvider(snapshot()), display).tick(T0)
    assert report.fresh and report.errors == ("build",)
    assert display.frames == []


def test_run_survives_unexpected_errors_and_reports_readiness():
    stop = InstantStop()
    notifier = FakeNotifier()
    display = MemoryDisplay()
    app = App(CONFIG, stop_after(3, stop, RuntimeError("bug")), display, notifier=notifier)
    assert app.run(stop, handle_signals=False) == 0
    assert len(stop.waits) >= 2  # carried on after each failure
    assert notifier.sent[0] == "READY=1"
    assert notifier.sent[-1] == "STOPPING=1"
    assert notifier.sent.count("WATCHDOG=1") >= 3  # after every tick
    assert display.closed


def test_repeated_display_failures_exit_non_zero(caplog):
    stop = InstantStop(FakeTime(T0))
    notifier = FakeNotifier()
    display = MemoryDisplay(*[True] * 10)
    app = App(
        CONFIG, FakeProvider(*[snapshot()] * 10), display, notifier=notifier, clock=stop.clock
    )
    with caplog.at_level(logging.ERROR, logger="zurich_air.app"):
        assert app.run(stop, handle_signals=False) == 1
    assert len(display.failures) == 10 - MAX_DISPLAY_FAILURES
    assert "times in a row" in caplog.text
    assert notifier.sent[-1] == "STOPPING=1"
    assert display.closed


def test_a_display_that_recovers_keeps_running():
    stop = InstantStop(FakeTime(T0))  # the clock moves on, so every frame is new
    display = MemoryDisplay(True, True, False, True, True, False)  # never 3 in a row
    app = App(CONFIG, stop_after(6, stop), display, notifier=FakeNotifier(), clock=stop.clock)
    assert app.run(stop, handle_signals=False) == 0
    assert display.failures == []
    assert len(display.frames) == 2


def test_closing_a_broken_display_does_not_raise(caplog):
    class BrokenClose(MemoryDisplay):
        def close(self):
            raise OSError("GPIO busy")

    App(CONFIG, FakeProvider(), BrokenClose()).close()
    assert "closing the display failed" in caplog.text


def test_sigterm_mid_sleep_stops_quickly():
    notifier = FakeNotifier()

    class Display(MemoryDisplay):
        def show(self, image):
            super().show(image)
            threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM)).start()

    def guard(signum, frame):  # catches the signal should the app have stopped already
        pass

    app = App(CONFIG, FakeProvider(snapshot()), Display(), notifier=notifier, clock=lambda: T0)
    previous = signal.signal(signal.SIGTERM, guard)
    try:
        start = time.monotonic()
        assert app.run() == 0  # would otherwise sleep until the next minute
        assert time.monotonic() - start < 5
        assert signal.getsignal(signal.SIGTERM) is guard  # restored
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert notifier.sent[-1] == "STOPPING=1"
    assert app.display.closed


# Quiet hours and the watchdog ---------------------------------------------------------


def test_watchdog_is_told_during_long_quiet_hour_sleeps():
    clock = FakeTime(datetime(2026, 12, 24, 2, 0, 1, tzinfo=ZRH))  # quiet hours
    stop = InstantStop(clock)
    notifier = FakeNotifier()
    app = App(CONFIG, stop_after(2, stop), MemoryDisplay(), notifier=notifier, clock=clock)
    app.run(stop, handle_signals=False)
    assert sum(stop.waits) == pytest.approx(CONFIG.update.quiet_interval_s)
    assert max(stop.waits) <= WATCHDOG_PING_S
    assert notifier.sent.count("WATCHDOG=1") >= CONFIG.update.quiet_interval_s / WATCHDOG_PING_S
    assert clock.now.astimezone(ZRH).time().isoformat() == "02:10:01"


def test_next_delay_switches_interval_and_logs_quiet_hours(caplog):
    app = App(CONFIG, FakeProvider(), MemoryDisplay())
    evening = datetime(2026, 12, 24, 23, 29, 1, tzinfo=ZRH)
    with caplog.at_level(logging.INFO, logger="zurich_air.app"):
        assert app.next_delay(evening) == pytest.approx(60)
        assert app.next_delay(evening + timedelta(minutes=1)) == pytest.approx(600)
        assert app.next_delay(evening + timedelta(hours=6, minutes=31)) == pytest.approx(60)
    assert "quiet hours until 06:00: updating every 600 s" in caplog.text
    assert "quiet hours over: updating every 60 s" in caplog.text


def test_quiet_hours_can_be_disabled():
    config = dataclasses.replace(CONFIG, update=dataclasses.replace(CONFIG.update, quiet_hours=""))
    app = App(config, FakeProvider(), MemoryDisplay())
    assert app.next_delay(datetime(2026, 12, 24, 2, 0, 1, tzinfo=ZRH)) == pytest.approx(60)


# Clock sanity -------------------------------------------------------------------------


def test_unset_clock_skips_fetching_and_retries_soon(caplog):
    class NtpArrives(InstantStop):
        def wait(self, timeout=None):
            if len(self.waits) == 1:  # during the second wait
                self.clock.now = T0
            return super().wait(timeout)

    clock = FakeTime(datetime(2025, 1, 1, 0, 0, 5, tzinfo=UTC))  # Pi booted without NTP
    stop = NtpArrives(clock)
    display = MemoryDisplay()
    app = App(CONFIG, stop_after(1, stop), display, notifier=FakeNotifier(), clock=clock)
    with caplog.at_level(logging.INFO, logger="zurich_air.app"):
        app.run(stop, handle_signals=False)
    assert stop.waits == [CLOCK_RETRY_S, CLOCK_RETRY_S]
    warnings = [r for r in caplog.records if "system clock reads 2025-01-01" in r.message]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    assert "system clock is set now" in caplog.text
    assert len(display.frames) == 1  # drawn only once the clock was right


def test_tick_with_unset_clock_does_nothing():
    provider = FakeProvider(snapshot())
    display = MemoryDisplay()
    report = App(CONFIG, provider, display).tick(datetime(1970, 1, 1, tzinfo=UTC))
    assert report.clock_wrong
    assert provider.calls == [] and display.frames == []


# Heartbeat logging --------------------------------------------------------------------


def test_one_heartbeat_line_per_tick(caplog):
    app = App(CONFIG, FakeProvider(snapshot(), ProviderError("no data: adsb.fi: timed out")),
              MemoryDisplay())  # fmt: skip
    with caplog.at_level(logging.DEBUG, logger="zurich_air.app"):
        app.tick(T0)
        app.tick(T0 + timedelta(seconds=30))
    lines = [r for r in caplog.records if r.message.startswith("update: ")]
    assert [(r.levelno, r.message) for r in lines] == [
        (logging.INFO, "update: adsb.fi, live, 1 aircraft, featured SWR001, display refreshed"),
        (
            logging.WARNING,
            "update: no new data, live, 1 aircraft, featured SWR001, display unchanged "
            "(no data: adsb.fi: timed out)",
        ),
    ]


def test_report_text_lists_failed_stages():
    report = TickReport(fresh=True, source="adsb.lol", status="live", errors=("display",))
    assert str(report) == "update: adsb.lol, live, 0 aircraft, display unchanged; failed: display"


class Ticker:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_hourly_summary():
    clock = Ticker()
    summary = HourlySummary(clock=clock)
    ok = TickReport(fresh=True, source="adsb.fi", status="live", refreshed=True)
    stale = TickReport(problem="down", status="stale", errors=("fetch",))
    assert summary.add(ok, {"adsb.fi": 0, "adsb.lol": 0}) is None
    clock.t = 1800
    assert summary.add(stale, {"adsb.fi": 2, "adsb.lol": 0}) is None
    clock.t = 3600
    line = summary.add(TickReport(clock_wrong=True), {"adsb.fi": 3, "adsb.lol": 1})
    assert line == (
        "last hour: 2 updates, 1 with new data, 1 display refreshes, 1 errors, "
        "1 skipped (clock not set); failed requests: adsb.fi 3, adsb.lol 1"
    )
    clock.t = 7200  # counts start afresh; failures are reported per hour
    line = summary.add(ok, {"adsb.fi": 3, "adsb.lol": 2})
    assert line == (
        "last hour: 1 updates, 1 with new data, 1 display refreshes, 0 errors; "
        "failed requests: adsb.fi 0, adsb.lol 1"
    )


def test_summary_without_source_counts():
    clock = Ticker()
    summary = HourlySummary(every_s=60, clock=clock)
    clock.t = 60
    assert summary.add(TickReport(), {}) == (
        "last hour: 1 updates, 0 with new data, 0 display refreshes, 0 errors"
    )


def test_tracebacks_are_rate_limited(caplog):
    clock = Ticker()
    errors = ErrorLog(every_s=3600, clock=clock)
    with caplog.at_level(logging.ERROR, logger="zurich_air.app"):
        for t in (0, 60, 120):
            clock.t = t
            errors.report("render", ValueError("x" * 500))
        errors.report("display", ValueError("other stage"))  # another kind: own traceback
        clock.t = 3600
        errors.report("render", ValueError("still"))
    with_traceback = [r.message for r in caplog.records if r.exc_info]
    assert len(caplog.records) == 5
    assert len(with_traceback) == 3
    assert all(len(r.message) < 300 for r in caplog.records)  # long messages are cut
