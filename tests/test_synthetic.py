from datetime import datetime

import pytest

from zurich_air.config import Config
from zurich_air.data import synthetic
from zurich_air.processing.geo import distance_km

CONFIG = Config()
CENTRE = (CONFIG.location.latitude, CONFIG.location.longitude)


@pytest.mark.parametrize("name", synthetic.SCENE_NAMES)
def test_scene_is_deterministic(name):
    assert synthetic.scene(name) == synthetic.scene(name)


@pytest.mark.parametrize("name", synthetic.SCENE_NAMES)
def test_scene_is_within_radar_range(name):
    for a in synthetic.scene(name).snapshot.aircraft:
        assert distance_km(*CENTRE, a.lat, a.lon) <= CONFIG.radar.range_km, a


@pytest.mark.parametrize("name", synthetic.SCENE_NAMES)
def test_scene_identities_and_trails(name):
    data = synthetic.scene(name)
    icaos = [a.icao for a in data.snapshot.aircraft]
    assert len(icaos) == len(set(icaos))
    assert set(data.trails) <= set(icaos)
    for trail in data.trails.values():
        assert 4 <= len(trail) <= 6


@pytest.mark.parametrize(
    ("name", "low", "high"),
    [("typical", 20, 24), ("busy", 40, 50), ("quiet", 3, 3), ("empty", 0, 0)],
)
def test_scene_sizes(name, low, high):
    assert low <= len(synthetic.scene(name).snapshot.aircraft) <= high


def test_timestamp_is_fixed_and_aware():
    data = synthetic.scene("typical")
    assert data.snapshot.timestamp == data.now
    assert data.now.utcoffset() is not None
    assert data.now.replace(tzinfo=None) == datetime(2026, 12, 24, 15, 42)


def test_stale_scene_data_is_older_than_now():
    data = synthetic.scene("stale")
    assert (data.now - data.snapshot.timestamp).total_seconds() > 300


def test_arrivals_on_final_descend_towards_the_airport():
    finals = [a for a in synthetic.scene("typical").snapshot.aircraft if a.track_deg == 137.0]
    assert finals
    for a in finals:
        assert a.altitude_ft is not None and a.altitude_ft < 8000
        assert a.vertical_rate_fpm is not None and a.vertical_rate_fpm < 0


def test_degraded_scene_has_missing_fields():
    aircraft = synthetic.scene("degraded").snapshot.aircraft
    assert any(a.callsign is None and a.registration is None for a in aircraft)
    assert any(a.altitude_ft is None for a in aircraft)
    assert any(a.track_deg is None for a in aircraft)
    assert any(a.type_code is None for a in aircraft)


def test_unknown_scene():
    with pytest.raises(ValueError, match="unknown scene"):
        synthetic.scene("rush-hour")
