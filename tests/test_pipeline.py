import dataclasses
from datetime import UTC, datetime, timedelta

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft, TrafficSnapshot
from zurich_air.pipeline import build_scene
from zurich_air.processing.geo import destination
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
    assert scene.status == "waiting"
    assert scene.aircraft == [] and scene.featured == []
    assert scene.updated_at is None  # never any data: no "since" to show
    assert scene.source_label is None
    assert scene.now == T0


def test_long_outage_hides_the_frozen_traffic():
    history = TrackHistory()
    data = snapshot()
    history.update(snapshot(T0 - timedelta(minutes=1)))
    history.update(data)
    hide_after = timedelta(seconds=CONFIG.update.hide_after_s)

    still = build_scene(data, history, CONFIG, T0 + hide_after, T0)
    assert still.status == "stale"
    assert len(still.aircraft) == 2

    later = T0 + hide_after + timedelta(seconds=1)
    hidden = build_scene(data, history, CONFIG, later, T0, previous_featured=("4b0001",))
    assert hidden.status == "waiting"
    assert hidden.aircraft == [] and hidden.featured == [] and hidden.trails == {}
    assert hidden.updated_at == T0  # "NO DATA SINCE" the last good data
    assert hidden.now == later
    assert hidden.source_label is None


def test_hide_threshold_is_configurable():
    config = dataclasses.replace(
        CONFIG, update=dataclasses.replace(CONFIG.update, hide_after_s=CONFIG.update.stale_after_s)
    )
    late = T0 + timedelta(seconds=CONFIG.update.stale_after_s + 1)
    assert build_scene(snapshot(), None, config, late, T0).status == "waiting"


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


AIRPORT = (CONFIG.airport.latitude, CONFIG.airport.longitude)


def arrival(icao, dist_km, **kwargs):
    lat, lon = destination(*AIRPORT, 317, dist_km)
    return Aircraft(
        icao=icao, lat=lat, lon=lon, callsign="SWR1", altitude_ft=1500 + round(dist_km * 320),
        track_deg=137, vertical_rate_fpm=-800, **kwargs,
    )  # fmt: skip


def test_scene_exposes_flight_phases_of_featured_aircraft():
    overflight = Aircraft(icao="4b0002", lat=LAT, lon=LON + 0.1, altitude_ft=36000)
    scene = build_scene(snapshot(T0, arrival("4b0001", 10), overflight), None, CONFIG, now=T0)
    assert [a.icao for a in scene.featured] == ["4b0001", "4b0002"]
    assert scene.phases == {"4b0001": "arrival"}


def test_previous_featured_keeps_the_list_calm():
    one = dataclasses.replace(CONFIG, aircraft=dataclasses.replace(CONFIG.aircraft, featured=1))
    data = snapshot(T0, arrival("4b0001", 20), arrival("4b0002", 15))
    assert [a.icao for a in build_scene(data, None, one, now=T0).featured] == ["4b0002"]
    calm = build_scene(data, None, one, now=T0, previous_featured=("4b0001",))
    assert [a.icao for a in calm.featured] == ["4b0001"]


def test_privacy_aircraft_plotted_but_never_featured():
    private = arrival("4b0001", 5, privacy=True)
    scene = build_scene(snapshot(T0, private, arrival("4b0002", 30)), None, CONFIG, now=T0)
    assert [a.icao for a in scene.aircraft] == ["4b0001", "4b0002"]
    assert [a.icao for a in scene.featured] == ["4b0002"]
