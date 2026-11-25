from datetime import UTC, datetime, timedelta

import pytest

from zurich_air.data.aircraft import Aircraft, TrafficSnapshot
from zurich_air.processing.history import TrackHistory

T0 = datetime(2026, 12, 24, 14, 42, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def snap(at, *aircraft):
    return TrafficSnapshot(aircraft=tuple(aircraft), timestamp=at, source="test")


def ac(icao, lat, lon=8.5, **kwargs):
    return Aircraft(icao=icao, lat=lat, lon=lon, **kwargs)


def test_trail_is_previous_positions_oldest_first():
    history = TrackHistory()
    for k in range(3):
        history.update(snap(T0 + k * MINUTE, ac("4b0001", 47.0 + k * 0.1)))
    now = ac("4b0001", 47.2)
    assert history.trail(now) == ((47.0, 8.5), (47.1, 8.5))
    assert history.trails([now, ac("4b0002", 47.0)]) == {"4b0001": ((47.0, 8.5), (47.1, 8.5))}


def test_single_sighting_has_no_trail():
    history = TrackHistory()
    history.update(snap(T0, ac("4b0001", 47.0)))
    assert history.trail(ac("4b0001", 47.0)) == ()
    assert history.trails([ac("4b0001", 47.0)]) == {}


def test_repeated_position_not_duplicated():
    history = TrackHistory()
    history.update(snap(T0, ac("4b0001", 47.0)))
    history.update(snap(T0 + MINUTE, ac("4b0001", 47.1)))
    history.update(snap(T0 + 2 * MINUTE, ac("4b0001", 47.1)))
    assert history.trail(ac("4b0001", 47.1)) == ((47.0, 8.5),)


def test_position_age_sets_the_time_and_out_of_order_is_ignored():
    history = TrackHistory()
    history.update(snap(T0, ac("4b0001", 47.0)))
    # Reported a minute later, but the position is 90 s old: older than what we have.
    history.update(snap(T0 + MINUTE, ac("4b0001", 47.05, position_age_s=90)))
    history.update(snap(T0 + 2 * MINUTE, ac("4b0001", 47.1)))
    assert history.trail(ac("4b0001", 47.1)) == ((47.0, 8.5),)


def test_points_bounded_per_aircraft():
    history = TrackHistory(max_points=4)
    for k in range(10):
        history.update(snap(T0 + k * MINUTE / 6, ac("4b0001", 47.0 + k * 0.01)))
    trail = history.trail(ac("4b0001", 47.09))
    assert len(trail) == 3
    assert trail[0] == pytest.approx((47.06, 8.5))


def test_old_points_expire():
    history = TrackHistory(max_age_s=120)
    lats = [47.0 + k * 0.1 for k in range(5)]
    for k, lat in enumerate(lats):
        history.update(snap(T0 + k * MINUTE, ac("4b0001", lat)))
    assert history.trail(ac("4b0001", lats[4])) == ((lats[2], 8.5), (lats[3], 8.5))


def test_aircraft_not_seen_are_forgotten():
    history = TrackHistory(forget_after_s=180)
    history.update(snap(T0, ac("4b0001", 47.0), ac("4b0002", 47.5)))
    for k in range(1, 5):
        history.update(snap(T0 + k * MINUTE, ac("4b0002", 47.5 + k * 0.01)))
    assert "4b0001" not in history
    assert "4b0002" in history
    assert len(history) == 1


def test_number_of_aircraft_bounded():
    history = TrackHistory(max_aircraft=10)
    for k in range(25):
        history.update(snap(T0 + k * timedelta(seconds=1), ac(f"{k:06x}", 47.0)))
    assert len(history) == 10
    assert f"{24:06x}" in history and f"{0:06x}" not in history


def test_invalid_bounds_rejected():
    with pytest.raises(ValueError):
        TrackHistory(max_points=0)
