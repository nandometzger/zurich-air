import threading
from datetime import UTC, datetime, timedelta

import pytest

from zurich_air.app import App, seconds_until_next_tick
from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft, TrafficSnapshot
from zurich_air.data.provider import ProviderError

CONFIG = Config()
T0 = datetime(2026, 12, 24, 14, 42, 1, tzinfo=UTC)
LAT, LON = CONFIG.location.latitude, CONFIG.location.longitude


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
    def __init__(self) -> None:
        self.frames = []
        self.closed = False

    def show(self, image):
        self.frames.append(image.copy())

    def close(self):
        self.closed = True


def test_tick_fetches_renders_and_shows():
    provider = FakeProvider(snapshot())
    display = MemoryDisplay()
    app = App(CONFIG, provider, display)
    assert app.tick(T0) is True
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
    app.tick(T0 + timedelta(seconds=30))  # same minute on the clock, same traffic
    assert len(display.frames) == 1


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
    assert app.tick(T0 + timedelta(seconds=60)) is False
    assert app.snapshot is data
    assert app.last_success == T0

    stale_at = T0 + timedelta(seconds=CONFIG.update.stale_after_s + 1)
    app.tick(stale_at)
    assert app.snapshot is data
    # The stale frame keeps the aircraft but differs from the live one (indicator, footer).
    assert display.frames[-1].tobytes() != display.frames[0].tobytes()


def test_failure_without_any_data_shows_stale_empty_frame():
    display = MemoryDisplay()
    app = App(CONFIG, FakeProvider(ProviderError("down")), display)
    assert app.tick(T0) is False
    assert app.snapshot is None
    assert len(display.frames) == 1


def test_history_builds_trails_across_ticks():
    app = App(
        CONFIG, FakeProvider(snapshot(), snapshot(T0 + timedelta(minutes=1), 0.2)), MemoryDisplay()
    )
    app.tick(T0)
    app.tick(T0 + timedelta(minutes=1))
    assert app.history.trail(app.snapshot.aircraft[0]) == ((LAT + 0.1, LON),)


def test_run_stops_on_event_and_closes_display():
    stop = threading.Event()

    class StoppingProvider(FakeProvider):
        def fetch(self, lat, lon, radius_km):
            stop.set()
            return super().fetch(lat, lon, radius_km)

    display = MemoryDisplay()
    App(CONFIG, StoppingProvider(snapshot()), display).run(stop, handle_signals=False)
    assert len(display.frames) == 1
    assert display.closed


def test_run_survives_unexpected_errors():
    stop = threading.Event()
    calls = []

    class BrokenProvider:
        def fetch(self, lat, lon, radius_km):
            calls.append(1)
            stop.set()
            raise RuntimeError("bug")

    display = MemoryDisplay()
    App(CONFIG, BrokenProvider(), display).run(stop, handle_signals=False)
    assert calls == [1]
    assert display.closed


@pytest.mark.parametrize(
    ("now", "delay"),
    [
        (1.0, 60.0),  # ticks land 1 s past each whole minute
        (10.5, 50.5),
        (58.5, 2.5),
        (59.5, 61.5),  # too close to the next slot: skip it rather than burst
        (60.9, 60.1),
    ],
)
def test_ticks_align_to_interval(now, delay):
    assert seconds_until_next_tick(now, 60.0) == pytest.approx(delay)
