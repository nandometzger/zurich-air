import dataclasses
from datetime import UTC, datetime, timedelta

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft, TrafficSnapshot
from zurich_air.pipeline import build_scene
from zurich_air.processing.history import TrackHistory

CONFIG = Config()
T0 = datetime(2026, 12, 24, 14, 42, tzinfo=UTC)
LAT, LON = CONFIG.location.latitude, CONFIG.location.longitude


def snapshot(at=T0, *aircraft, source="adsb.fi"):
    if not aircraft:
        aircraft = (
            Aircraft(icao="4b0001", lat=LAT + 0.1, lon=LON, altitude_ft=5000),
            Aircraft(icao="4b0002", lat=LAT, lon=LON + 0.1, altitude_ft=36000),
            Aircraft(icao="4b0003", lat=47.457, lon=8.555, on_ground=True),
            Aircraft(icao="4b0004", lat=LAT + 2.0, lon=LON, altitude_ft=36000),  # far away
        )
    return TrafficSnapshot(aircraft=tuple(aircraft), timestamp=at, source=source)


def test_live_scene_filters_ranks_and_attributes():
    scene = build_scene(snapshot(), None, CONFIG, now=T0 + timedelta(seconds=5))
    assert [a.icao for a in scene.aircraft] == ["4b0001", "4b0002"]
    assert [a.icao for a in scene.featured] == ["4b0001", "4b0002"]
    assert scene.status == "live"
    assert scene.source_label == "ADSB.FI"
    assert scene.updated_at == T0
    assert scene.now == T0 + timedelta(seconds=5)
    assert scene.trails == {}


def test_stale_when_last_success_is_old():
    stale_after = timedelta(seconds=CONFIG.update.stale_after_s)
    data = snapshot()
    fetched = T0
    assert build_scene(data, None, CONFIG, T0 + stale_after, fetched).status == "live"
    late = T0 + stale_after + timedelta(seconds=1)
    scene = build_scene(data, None, CONFIG, late, fetched)
    assert scene.status == "stale"
    assert scene.updated_at == T0
    assert len(scene.aircraft) == 2  # the last data stays on screen, marked stale


def test_staleness_uses_local_fetch_time_not_provider_clock():
    data = snapshot(at=T0 - timedelta(hours=1))  # provider clock an hour behind
    assert build_scene(data, None, CONFIG, now=T0, last_success=T0).status == "live"


def test_without_fetch_time_snapshot_time_decides():
    data = snapshot(at=T0 - timedelta(minutes=11))
    assert build_scene(data, None, CONFIG, now=T0).status == "stale"


def test_no_data_at_all():
    scene = build_scene(None, TrackHistory(), CONFIG, now=T0)
    assert scene.status == "stale"
    assert scene.aircraft == [] and scene.featured == []
    assert scene.source_label is None


def test_trails_from_history_only_for_visible_aircraft():
    history = TrackHistory()
    earlier = snapshot(
        T0 - timedelta(minutes=1),
        Aircraft(icao="4b0001", lat=LAT + 0.05, lon=LON),
        Aircraft(icao="4b0004", lat=LAT + 1.9, lon=LON),
    )
    now = snapshot()
    history.update(earlier)
    history.update(now)
    scene = build_scene(now, history, CONFIG, now=T0)
    assert scene.trails == {"4b0001": ((LAT + 0.05, LON),)}

    no_trails = dataclasses.replace(CONFIG, radar=dataclasses.replace(CONFIG.radar, trails=False))
    assert build_scene(now, history, no_trails, now=T0).trails == {}
