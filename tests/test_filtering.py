import dataclasses

from zurich_air.config import AircraftConfig, Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing.filtering import dedupe, is_on_ground, visible_aircraft

CONFIG = Config()
CITY = (CONFIG.location.latitude, CONFIG.location.longitude)


def ac(icao="4b0001", lat=CITY[0], lon=CITY[1], **kwargs):
    return Aircraft(icao=icao, lat=lat, lon=lon, **kwargs)


def with_aircraft(**kwargs):
    return dataclasses.replace(CONFIG, aircraft=dataclasses.replace(CONFIG.aircraft, **kwargs))


def test_keeps_ordinary_traffic_in_order():
    traffic = [ac("4b0002", altitude_ft=5000), ac("4b0001", altitude_ft=36000)]
    assert visible_aircraft(traffic, CONFIG) == traffic


def test_ground_traffic_dropped_unless_wanted():
    taxiing = ac(on_ground=True)
    assert visible_aircraft([taxiing], CONFIG) == []
    assert visible_aircraft([taxiing], with_aircraft(include_ground=True)) == [taxiing]


def test_low_and_stationary_counts_as_on_ground():
    # A helicopter on a pad reporting "airborne" at 1350 ft and 0.7 kt (ZRH is at 1416 ft).
    on_pad = ac("4b0001", altitude_ft=1350, ground_speed_kt=0.7, category="A7")
    hovering_high = ac("4b0002", altitude_ft=6000, ground_speed_kt=0.0, category="A7")
    flying_low = ac("4b0003", altitude_ft=1800, ground_speed_kt=90.0)
    speed_unknown = ac("4b0004", altitude_ft=1350)
    altitude_unknown = ac("4b0005", ground_speed_kt=0.5)
    traffic = [on_pad, hovering_high, flying_low, speed_unknown, altitude_unknown]
    assert is_on_ground(on_pad, CONFIG)
    assert visible_aircraft(traffic, CONFIG) == traffic[1:]
    assert visible_aircraft([on_pad], with_aircraft(include_ground=True)) == [on_pad]


def test_ground_margin_follows_field_elevation():
    high_field = dataclasses.replace(
        CONFIG, airport=dataclasses.replace(CONFIG.airport, elevation_ft=5600)
    )
    parked = ac(altitude_ft=5800, ground_speed_kt=2.0)
    assert is_on_ground(parked, high_field)
    assert not is_on_ground(parked, CONFIG)


def test_vehicles_and_obstacles_never_shown():
    config = with_aircraft(include_ground=True)
    vehicle = ac("4b0001", on_ground=True, category="C2", type_code="GND")
    helicopter = ac("4b0002", category="A7")
    unknown = ac("4b0003")
    assert visible_aircraft([vehicle, helicopter, unknown], config) == [helicopter, unknown]


def test_old_positions_dropped():
    assert visible_aircraft([ac(position_age_s=60.0)], CONFIG)
    assert visible_aircraft([ac(position_age_s=61.0)], CONFIG) == []


def test_altitude_ceiling_keeps_unknown_altitude():
    config = with_aircraft(max_altitude_ft=20_000)
    traffic = [ac("4b0001", altitude_ft=20_000), ac("4b0002", altitude_ft=20_100), ac("4b0003")]
    assert [a.icao for a in visible_aircraft(traffic, config)] == ["4b0001", "4b0003"]


def test_outside_radar_range_dropped():
    inside = ac("4b0001", lat=CITY[0] + 0.53)  # ~59 km north
    outside = ac("4b0002", lat=CITY[0] + 0.56)  # ~62 km north
    assert visible_aircraft([inside, outside], CONFIG) == [inside]


def test_dedupe_keeps_freshest_position_in_first_seen_order():
    old = ac("4b0001", lat=47.0, position_age_s=10.0)
    other = ac("4b0002")
    new = ac("4b0001", lat=47.1, position_age_s=1.0)
    assert dedupe([old, other, new]) == [new, other]


def test_dedupe_tie_keeps_first():
    first, second = ac(callsign="A"), ac(callsign="B")
    assert dedupe([first, second]) == [first]


def test_defaults_match_documented_config():
    assert AircraftConfig().stale_after_s == 60.0
