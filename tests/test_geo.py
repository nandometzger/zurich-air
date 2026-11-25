import math

import pytest

from zurich_air.processing.geo import (
    RadarProjection,
    angle_diff,
    bearing_deg,
    cardinal,
    destination,
    distance_km,
    east_north_km,
)

ZRH_CITY = (47.3769, 8.5417)
ZRH_AIRPORT = (47.458056, 8.548056)
BERN = (46.9480, 7.4474)
GENEVA = (46.2044, 6.1432)


def test_distance_zero():
    assert distance_km(*ZRH_CITY, *ZRH_CITY) == 0.0


def test_distance_city_to_airport():
    # Zürich HB to the airport reference point is ~9 km.
    assert distance_km(*ZRH_CITY, *ZRH_AIRPORT) == pytest.approx(9.04, abs=0.1)


def test_distance_zurich_bern():
    # Reference value from an independent great-circle calculator: ~95.5 km.
    assert distance_km(*ZRH_CITY, *BERN) == pytest.approx(95.5, abs=0.5)


def test_distance_symmetry():
    assert distance_km(*ZRH_CITY, *GENEVA) == pytest.approx(distance_km(*GENEVA, *ZRH_CITY))


def test_one_degree_latitude():
    # One degree of latitude on a sphere of radius R is R * pi / 180.
    assert distance_km(47.0, 8.0, 48.0, 8.0) == pytest.approx(111.195, abs=0.01)


@pytest.mark.parametrize(
    ("dlat", "dlon", "expected"),
    [(0.1, 0.0, 0.0), (0.0, 0.1, 90.0), (-0.1, 0.0, 180.0), (0.0, -0.1, 270.0)],
)
def test_bearing_cardinal_directions(dlat, dlon, expected):
    lat, lon = ZRH_CITY
    b = bearing_deg(lat, lon, lat + dlat, lon + dlon)
    assert angle_diff(b, expected) == pytest.approx(0.0, abs=0.1)


def test_bearing_to_airport_is_north():
    assert bearing_deg(*ZRH_CITY, *ZRH_AIRPORT) == pytest.approx(3.0, abs=1.0)


def test_bearing_to_bern_is_southwest():
    assert 230 < bearing_deg(*ZRH_CITY, *BERN) < 250


@pytest.mark.parametrize("bearing", [0, 45, 137, 200, 315])
@pytest.mark.parametrize("dist", [1.0, 25.0, 80.0])
def test_destination_roundtrip(bearing, dist):
    lat, lon = destination(*ZRH_CITY, bearing, dist)
    assert distance_km(*ZRH_CITY, lat, lon) == pytest.approx(dist, rel=1e-9)
    assert angle_diff(bearing_deg(*ZRH_CITY, lat, lon), bearing) == pytest.approx(0, abs=1e-6)


def test_east_north_axes():
    east, north = east_north_km(*ZRH_CITY, *destination(*ZRH_CITY, 90, 30))
    assert east == pytest.approx(30, abs=1e-6)
    assert north == pytest.approx(0, abs=1e-6)
    east, north = east_north_km(*ZRH_CITY, *destination(*ZRH_CITY, 180, 30))
    assert east == pytest.approx(0, abs=1e-6)
    assert north == pytest.approx(-30, abs=1e-6)


def test_east_north_preserves_distance():
    east, north = east_north_km(*ZRH_CITY, *BERN)
    assert math.hypot(east, north) == pytest.approx(distance_km(*ZRH_CITY, *BERN))


@pytest.mark.parametrize(
    ("a", "b", "expected"), [(10, 350, 20), (350, 10, -20), (180, 0, 180), (90, 90, 0)]
)
def test_angle_diff(a, b, expected):
    assert angle_diff(a, b) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("bearing", "name"),
    [(0, "N"), (22, "N"), (23, "NE"), (90, "E"), (180, "S"), (225, "SW"), (300, "NW"), (359, "N")],
)
def test_cardinal(bearing, name):
    assert cardinal(bearing) == name


@pytest.fixture
def radar():
    return RadarProjection(*ZRH_CITY, range_km=80, center_x=130, center_y=150, radius_px=112)


def test_radar_center_maps_to_center(radar):
    assert radar.to_screen(*ZRH_CITY) == pytest.approx((130, 150))


def test_radar_north_is_up_east_is_right(radar):
    x, y = radar.to_screen(*destination(*ZRH_CITY, 0, 40))
    assert x == pytest.approx(130, abs=1e-6)
    assert y == pytest.approx(150 - 56, abs=1e-6)
    x, y = radar.to_screen(*destination(*ZRH_CITY, 90, 40))
    assert x == pytest.approx(130 + 56, abs=1e-6)
    assert y == pytest.approx(150, abs=1e-6)


def test_radar_edge_is_radius(radar):
    x, y = radar.to_screen(*destination(*ZRH_CITY, 237, 80))
    assert math.hypot(x - 130, y - 150) == pytest.approx(112, abs=1e-6)


def test_radar_in_range(radar):
    assert radar.in_range(*destination(*ZRH_CITY, 10, 79.9))
    assert not radar.in_range(*destination(*ZRH_CITY, 10, 80.1))


def test_radar_other_location():
    bern = RadarProjection(*BERN, range_km=50, center_x=0, center_y=0, radius_px=100)
    assert bern.to_screen(*BERN) == pytest.approx((0, 0))
    assert not bern.in_range(*ZRH_CITY)
