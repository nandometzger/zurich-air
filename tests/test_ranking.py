import dataclasses

from zurich_air.config import Config
from zurich_air.data import synthetic
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing.geo import destination
from zurich_air.processing.ranking import featured_score, select_featured

CONFIG = Config()
AIRPORT = (CONFIG.airport.latitude, CONFIG.airport.longitude)


def at(bearing, dist_km, **kwargs):
    lat, lon = destination(*AIRPORT, bearing, dist_km)
    return Aircraft(icao=kwargs.pop("icao", "4b0001"), lat=lat, lon=lon, **kwargs)


def test_low_and_near_beats_high_overflight():
    arrival = at(317, 15, icao="a", altitude_ft=4000)
    overflight = at(317, 15, icao="b", altitude_ft=37000)
    assert featured_score(arrival, CONFIG) > featured_score(overflight, CONFIG)


def test_closer_is_better():
    assert featured_score(at(90, 5), CONFIG) > featured_score(at(90, 50), CONFIG)


def test_widebody_bonus():
    narrow = at(0, 30, type_code="A320", altitude_ft=35000)
    wide = dataclasses.replace(narrow, type_code="B77W")
    assert featured_score(wide, CONFIG) > featured_score(narrow, CONFIG)


def test_emergency_always_first():
    emergency = at(180, 75, icao="e", altitude_ft=38000, emergency=True)
    others = [at(10 * i, 5, icao=f"x{i}", altitude_ft=3000) for i in range(6)]
    assert select_featured([*others, emergency], CONFIG)[0] is emergency


def test_count_order_and_ground_filter():
    aircraft = [at(10 * i, 5 + 5 * i, icao=f"{i:06x}", altitude_ft=3000) for i in range(8)]
    parked = at(0, 0.5, icao="ground", on_ground=True)
    featured = select_featured([parked, *aircraft], CONFIG)
    assert len(featured) == CONFIG.aircraft.featured
    assert parked not in featured
    assert featured == aircraft[: CONFIG.aircraft.featured]
    assert select_featured(aircraft, CONFIG, n=2) == aircraft[:2]
    assert select_featured([], CONFIG) == []


def test_missing_fields_do_not_crash():
    assert featured_score(Aircraft("4b0001", *AIRPORT), CONFIG) > 0


def test_typical_scene_features_terminal_traffic():
    featured = select_featured(synthetic.scene("typical").snapshot.aircraft, CONFIG)
    assert all(a.altitude_ft is not None and a.altitude_ft < 10_000 for a in featured)
