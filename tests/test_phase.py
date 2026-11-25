import dataclasses

import pytest

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing.geo import destination
from zurich_air.processing.phase import flight_phase

CONFIG = Config()
AIRPORT = (CONFIG.airport.latitude, CONFIG.airport.longitude)


def at(bearing, dist_km, **kwargs):
    """An aircraft *dist_km* from the airport on *bearing* (as seen from the airport)."""
    lat, lon = destination(*AIRPORT, bearing, dist_km)
    return Aircraft(icao=kwargs.pop("icao", "4b0001"), lat=lat, lon=lon, **kwargs)


def inbound(bearing, dist_km, **kwargs):
    """Flying straight at the airport."""
    return at(bearing, dist_km, track_deg=(bearing + 180) % 360, **kwargs)


def outbound(bearing, dist_km, **kwargs):
    """Flying straight away from the airport."""
    return at(bearing, dist_km, track_deg=bearing, **kwargs)


def test_clear_arrival():
    # On the ILS 14 from the north-west, descending through 5000 ft.
    assert flight_phase(inbound(317, 15, altitude_ft=5000, vertical_rate_fpm=-800), CONFIG) == (
        "arrival"
    )


def test_clear_departure():
    # Off runway 16 to the south, climbing through 6000 ft.
    assert flight_phase(outbound(172, 10, altitude_ft=6000, vertical_rate_fpm=2500), CONFIG) == (
        "departure"
    )


def test_departure_through_11000_ft():
    climbing = outbound(200, 18, altitude_ft=11_000, vertical_rate_fpm=2000)
    assert flight_phase(climbing, CONFIG) == "departure"


def test_overflight_has_no_phase():
    cruise = at(90, 6, altitude_ft=39_000, vertical_rate_fpm=0, track_deg=240)
    assert flight_phase(cruise, CONFIG) is None


@pytest.mark.parametrize("missing", ["altitude_ft", "vertical_rate_fpm", "track_deg"])
def test_missing_data_has_no_phase(missing):
    arrival = inbound(317, 15, altitude_ft=5000, vertical_rate_fpm=-800)
    assert flight_phase(dataclasses.replace(arrival, **{missing: None}), CONFIG) is None


@pytest.mark.parametrize(
    ("aircraft", "why"),
    [
        (at(160, 15, altitude_ft=9000, vertical_rate_fpm=-800, track_deg=155), "downwind"),
        (inbound(317, 15, altitude_ft=11_000, vertical_rate_fpm=-800), "too high to arrive"),
        (inbound(317, 15, altitude_ft=5000, vertical_rate_fpm=0), "level, not very low"),
        (inbound(317, 15, altitude_ft=5000, vertical_rate_fpm=1500), "climbing inbound"),
        (outbound(172, 10, altitude_ft=6000, vertical_rate_fpm=-1500), "descending outbound"),
        (outbound(172, 10, altitude_ft=16_000, vertical_rate_fpm=2000), "too high to depart"),
        (outbound(172, 10, altitude_ft=6000, vertical_rate_fpm=200), "barely climbing"),
        (inbound(317, 45, altitude_ft=8000, vertical_rate_fpm=-1000), "too far out"),
    ],
)
def test_ambiguous_cases_have_no_phase(aircraft, why):
    assert flight_phase(aircraft, CONFIG) is None, why


def test_level_and_very_low_inbound_counts_as_arrival():
    flare = inbound(317, 2, altitude_ft=1600, vertical_rate_fpm=-100)
    assert flight_phase(flare, CONFIG) == "arrival"


def test_helicopters_and_parked_aircraft_have_no_phase():
    rega = inbound(317, 10, altitude_ft=3000, vertical_rate_fpm=-800, category="A7")
    parked = inbound(317, 1, altitude_ft=1400, vertical_rate_fpm=0, ground_speed_kt=0.5)
    on_ground = inbound(317, 1, altitude_ft=None, vertical_rate_fpm=0, on_ground=True)
    assert flight_phase(rega, CONFIG) is None
    assert flight_phase(parked, CONFIG) is None
    assert flight_phase(on_ground, CONFIG) is None


def test_thresholds_come_from_config():
    angled = at(317, 15, altitude_ft=5000, vertical_rate_fpm=-800, track_deg=137 + 60)
    assert flight_phase(angled, CONFIG) == "arrival"
    strict = dataclasses.replace(
        CONFIG, ranking=dataclasses.replace(CONFIG.ranking, max_track_offset_deg=45)
    )
    assert flight_phase(angled, strict) is None
    near = dataclasses.replace(
        CONFIG, ranking=dataclasses.replace(CONFIG.ranking, phase_radius_km=10)
    )
    assert flight_phase(angled, near) is None
