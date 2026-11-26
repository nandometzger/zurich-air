import dataclasses

import pytest

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.routes import Route
from zurich_air.processing.geo import bearing_deg, destination
from zurich_air.processing.phase import flight_phase
from zurich_air.processing.route_check import VerifiedRoute, verify_route

CONFIG = Config()
ZRH = (CONFIG.airport.latitude, CONFIG.airport.longitude)
CITY = (CONFIG.location.latitude, CONFIG.location.longitude)
AIRPORTS = {
    "LSZH": ZRH,
    "EGLL": (51.4706, -0.4619),
    "EGLC": (51.5053, 0.0553),
    "KJFK": (40.6398, -73.7789),
    "OMDB": (25.2528, 55.3644),
    "VHHH": (22.3089, 113.9150),
    "LFSB": (47.5896, 7.5299),
    "EDDM": (48.3538, 11.7861),
    "EDDF": (50.0333, 8.5706),
    "LIRF": (41.8003, 12.2389),
    # A made-up leg 600 km long, passing 160 km south of a point north of the city.
    "WEST": destination(*destination(*CITY, 0.0, 100.0), 242.0, 340.0),
    "EAST": destination(*destination(*CITY, 0.0, 100.0), 118.0, 340.0),
}


def at(ref, bearing, dist_km, **kwargs):
    """An aircraft *dist_km* from *ref* on *bearing*, airborne with a fresh position."""
    lat, lon = destination(*ref, bearing, dist_km)
    kwargs.setdefault("callsign", "SWR8")
    return Aircraft(icao=kwargs.pop("icao", "4b0001"), lat=lat, lon=lon, **kwargs)


def heading_for(aircraft, code, offset=0.0):
    """*aircraft* turned to fly towards airport *code* (plus *offset* degrees)."""
    track = (bearing_deg(aircraft.lat, aircraft.lon, *AIRPORTS[code]) + offset) % 360
    return dataclasses.replace(aircraft, track_deg=track)


def verify(aircraft, airports, phase=None, *, callsign=None, **kwargs):
    route = Route(callsign or aircraft.callsign or "SWR8", tuple(airports.split("-")))
    return verify_route(
        aircraft,
        route,
        airport_icao="LSZH",
        airport_lat=ZRH[0],
        airport_lon=ZRH[1],
        phase=phase,
        airport_position=AIRPORTS.get,
        **kwargs,
    )


def climbing_out(**kwargs):
    """Off runway 28 to the west, climbing through 6000 ft: a clear departure."""
    return at(ZRH, 265, 12, altitude_ft=6000, vertical_rate_fpm=2500, track_deg=265, **kwargs)


def on_final(**kwargs):
    """On the ILS 14 from the north-west, descending through 5000 ft: a clear arrival."""
    return at(ZRH, 317, 15, altitude_ft=5000, vertical_rate_fpm=-800, track_deg=137, **kwargs)


def cruising_south_east(**kwargs):
    """At FL370 over the city, heading for Dubai."""
    kwargs.setdefault("callsign", "UAE2")
    a = at(CITY, 300, 10, altitude_ft=37_000, vertical_rate_fpm=0, **kwargs)
    return heading_for(a, "OMDB", offset=5)


# -- departures and arrivals --------------------------------------------------------------


def test_departure_zrh_to_jfk_climbing_out():
    a = climbing_out()
    phase = flight_phase(a, CONFIG)
    assert phase == "departure"
    assert verify(a, "LSZH-KJFK", phase) == VerifiedRoute("LSZH", "KJFK", "departure")


def test_arrival_lhr_to_zrh_descending():
    a = on_final(callsign="BAW708")
    phase = flight_phase(a, CONFIG)
    assert phase == "arrival"
    assert verify(a, "EGLL-LSZH", phase) == VerifiedRoute("EGLL", "LSZH", "arrival")


def test_multi_leg_route_through_zrh_in_both_directions():
    departing, arriving = climbing_out(), on_final()
    assert verify(departing, "EGLL-LSZH-KJFK", "departure") == VerifiedRoute(
        "LSZH", "KJFK", "departure"
    )
    assert verify(arriving, "EGLL-LSZH-KJFK", "arrival") == VerifiedRoute("EGLL", "LSZH", "arrival")


def test_phase_contradicting_the_direction_of_the_route():
    # The callsign's route is the return flight: arriving per schedule, departing in fact.
    reasons = []
    assert verify(climbing_out(), "KJFK-LSZH", "departure", reasons=reasons) is None
    assert verify(on_final(), "LSZH-KJFK", "arrival", reasons=reasons) is None
    assert reasons == [
        "departure inferred but the route has no such leg at LSZH",
        "arrival inferred but the route has no such leg at LSZH",
    ]


def test_arrival_phase_but_route_without_zrh():
    reasons = []
    assert verify(on_final(), "EGLL-LFSB", "arrival", reasons=reasons) is None
    assert reasons == ["arrival inferred but LSZH is not on the route"]


def test_zrh_on_the_route_needs_a_phase():
    # Downwind, or just beyond phase_radius_km: no ARR/DEP, so the role of ZRH is unknown.
    reasons = []
    assert verify(climbing_out(), "LSZH-KJFK", None, reasons=reasons) is None
    assert reasons == ["LSZH on the route but no arrival or departure observed"]


def test_two_different_legs_out_of_zrh_are_ambiguous():
    reasons = []
    assert verify(climbing_out(), "LSZH-EGLL-LSZH-KJFK", "departure", reasons=reasons) is None
    assert reasons == ["ambiguous: 2 legs at LSZH"]


def test_circular_route_is_never_a_destination():
    assert verify(climbing_out(), "LSZH-LSZH", "departure") is None


# -- overflights --------------------------------------------------------------------------


def test_overflight_lhr_to_dxb_heading_south_east():
    a = cruising_south_east()
    assert verify(a, "EGLL-OMDB") == VerifiedRoute("EGLL", "OMDB", "overflight")


def test_same_callsign_heading_the_wrong_way():
    a = heading_for(cruising_south_east(), "EGLL")  # north-west, back towards London
    reasons = []
    assert verify(a, "EGLL-OMDB", reasons=reasons) is None
    assert reasons[0].startswith("EGLL-OMDB: track")
    assert verify(a, "OMDB-EGLL") == VerifiedRoute("OMDB", "EGLL", "overflight")


def test_multi_leg_overflight_picks_the_leg_that_fits():
    a = cruising_south_east()
    assert verify(a, "EGLL-OMDB-VHHH") == VerifiedRoute("EGLL", "OMDB", "overflight")


def test_multi_leg_overflight_with_two_fitting_legs_is_ambiguous():
    reasons = []
    assert verify(cruising_south_east(), "EGLL-OMDB-EGLC-OMDB", reasons=reasons) is None
    assert reasons == ["ambiguous: 2 legs fit"]


def test_track_tolerance():
    a = cruising_south_east()
    assert verify(heading_for(a, "OMDB", offset=29), "EGLL-OMDB") is not None
    assert verify(heading_for(a, "OMDB", offset=-31), "EGLL-OMDB") is None
    assert verify(heading_for(a, "OMDB", offset=40), "EGLL-OMDB", max_track_offset_deg=45)


def test_origin_must_be_behind():
    # Heading for Munich from over Zürich, with Frankfurt to the north: not behind.
    a = heading_for(at(CITY, 0, 5, altitude_ft=36_000, callsign="DLH1"), "EDDM")
    reasons = []
    assert verify(a, "EDDF-EDDM", reasons=reasons) is None
    assert "from behind" in reasons[0]


def test_no_progress_from_the_origin():
    # 100 km west of Basel and heading for Munich: the Basel-Munich flight has not begun.
    a = heading_for(at(AIRPORTS["LFSB"], 270, 100, altitude_ft=30_000, callsign="DLH1"), "EDDM")
    reasons = []
    assert verify(a, "LFSB-EDDM", reasons=reasons) is None
    assert reasons == ["LFSB-EDDM: no nearer EDDM than LFSB is"]


def test_long_detour_is_rejected():
    a = heading_for(at(CITY, 0, 100, altitude_ft=36_000, callsign="ABC1"), "EAST")
    reasons = []
    assert verify(a, "WEST-EAST", reasons=reasons) is None
    assert reasons == ["WEST-EAST: detour 1.13 times the direct distance"]
    assert verify(a, "WEST-EAST", max_detour_ratio=1.2) is not None


def test_low_aircraft_must_be_near_an_end_of_the_leg():
    low = dataclasses.replace(cruising_south_east(), altitude_ft=5000)
    reasons = []
    assert verify(low, "EGLL-OMDB", reasons=reasons) is None
    assert reasons == ["EGLL-OMDB: at 5000 ft but not near EGLL or OMDB"]


def test_low_aircraft_nearer_zrh_than_the_leg_is_left_to_zrh():
    # 20 km north of ZRH at 8000 ft, towards Munich on a Basel-Munich route.
    a = heading_for(at(ZRH, 0, 20, altitude_ft=8000, callsign="DLH1"), "EDDM")
    reasons = []
    assert verify(a, "LFSB-EDDM", reasons=reasons) is None
    assert reasons == ["LFSB-EDDM: at 8000 ft but not near LFSB or EDDM"]
    # The same 30 km out of Basel is a plausible climb out of Basel.
    climbing = at(AIRPORTS["LFSB"], bearing_deg(*AIRPORTS["LFSB"], *AIRPORTS["EDDM"]), 30)
    climbing = heading_for(dataclasses.replace(climbing, altitude_ft=8000, callsign="DLH1"), "EDDM")
    assert verify(climbing, "LFSB-EDDM") == VerifiedRoute("LFSB", "EDDM", "overflight")


def test_early_descent_is_for_somewhere_else():
    descending = dataclasses.replace(cruising_south_east(), vertical_rate_fpm=-1500)
    reasons = []
    assert verify(descending, "EGLL-OMDB", reasons=reasons) is None
    assert reasons[0].startswith("EGLL-OMDB: descending")
    # Into Frankfurt, 230 km ahead: a normal descent.
    a = heading_for(at(CITY, 0, 60, altitude_ft=30_000, vertical_rate_fpm=-1500), "EDDF")
    assert verify(a, "LIRF-EDDF") == VerifiedRoute("LIRF", "EDDF", "overflight")


def test_unknown_airport_cannot_be_checked():
    reasons = []
    assert verify(cruising_south_east(), "EGLL-ZZZZ", reasons=reasons) is None
    assert reasons == ["EGLL-ZZZZ: unknown airport ZZZZ"]


@pytest.mark.parametrize("missing", ["track_deg", "altitude_ft"])
def test_overflight_needs_track_and_altitude(missing):
    a = dataclasses.replace(cruising_south_east(), **{missing: None})
    assert verify(a, "EGLL-OMDB") is None


# -- aircraft that are not judged at all --------------------------------------------------


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"on_ground": True}, "on the ground"),
        ({"privacy": True}, "privacy-flagged"),
        ({"position_age_s": 61.0}, "stale position"),
        ({"callsign": None}, "no callsign"),
        ({"callsign": "UAE3"}, "route is for UAE2"),
    ],
)
def test_aircraft_that_are_not_judged(change, reason):
    a = dataclasses.replace(cruising_south_east(), **change)
    reasons = []
    assert verify(a, "EGLL-OMDB", callsign="UAE2", reasons=reasons) is None
    assert reasons == [reason]


def test_departure_privacy_flagged_is_not_judged():
    assert verify(climbing_out(privacy=True), "LSZH-KJFK", "departure") is None


def test_callsign_is_compared_normalised():
    a = dataclasses.replace(climbing_out(), callsign="swr008 ")
    assert verify(a, "LSZH-KJFK", "departure", callsign="SWR8") is not None


def test_no_route():
    reasons = []
    result = verify_route(
        climbing_out(),
        None,
        airport_icao="lszh",
        airport_lat=ZRH[0],
        airport_lon=ZRH[1],
        phase="departure",
        airport_position=AIRPORTS.get,
        reasons=reasons,
    )
    assert result is None
    assert reasons == ["no route"]
