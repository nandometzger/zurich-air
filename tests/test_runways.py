from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from zurich_air.config import ZRH_RUNWAYS, AirportConfig
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing.geo import angle_diff, bearing_deg, destination
from zurich_air.processing.history import TrackHistory
from zurich_air.processing.runways import (
    RunwayMonitor,
    RunwayTolerances,
    RunwayUse,
    classify,
    runway_ends,
)

ELEVATION = AirportConfig().elevation_ft
T0 = datetime(2026, 11, 26, 8, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class Rwy:
    le_ident: str
    le_lat: float
    le_lon: float
    he_ident: str
    he_lat: float
    he_lon: float


def designated(lat1: float, lon1: float, lat2: float, lon2: float) -> Rwy:
    """Name a runway by its bearing: "14" points roughly 140° from its threshold."""
    bearing = bearing_deg(lat1, lon1, lat2, lon2)
    for le, he in (("10", "28"), ("14", "32"), ("16", "34")):
        if abs(angle_diff(bearing, int(le) * 10)) < 10:
            return Rwy(le, lat1, lon1, he, lat2, lon2)
        if abs(angle_diff(bearing, int(he) * 10)) < 10:
            return Rwy(le, lat2, lon2, he, lat1, lon1)
    raise AssertionError(f"no designator for bearing {bearing:.0f}")


RUNWAYS = tuple(designated(*r) for r in ZRH_RUNWAYS)
ENDS = {end.ident: end for end in runway_ends(RUNWAYS)}


def point(ident: str, along_km: float, cross_km: float = 0.0) -> tuple[float, float]:
    """*along_km* past the threshold of *ident* (negative: before it), *cross_km* right."""
    heading = ENDS[ident].heading_deg
    lat, lon = ENDS[ident].lat, ENDS[ident].lon
    lat, lon = destination(lat, lon, heading if along_km >= 0 else heading + 180, abs(along_km))
    return destination(lat, lon, heading + (90 if cross_km >= 0 else -90), abs(cross_km))


def aircraft(icao, ident, along_km, cross_km=0.0, track_offset=0.0, **fields) -> Aircraft:
    """At *along_km* / *cross_km* from the threshold of *ident*, tracking down the runway."""
    lat, lon = point(ident, along_km, cross_km)
    fields.setdefault("track_deg", (ENDS[ident].heading_deg + track_offset) % 360)
    return Aircraft(icao=icao, lat=lat, lon=lon, **fields)


def landing(ident, km_out, icao="4b0001", cross_km=0.0, track_offset=0.0, **fields):
    """On a 3° approach to *ident*, *km_out* before the threshold."""
    fields = {
        "altitude_ft": round(ELEVATION + 50 + 172 * km_out),
        "vertical_rate_fpm": -700,
        "ground_speed_kt": 140.0,
        **fields,
    }
    return aircraft(icao, ident, -km_out, cross_km, track_offset, **fields)


def taking_off(ident, km_beyond_end, icao="4b0002", cross_km=0.0, track_offset=0.0, **fields):
    """Climbing out from *ident*, *km_beyond_end* past the far end of the runway."""
    fields = {
        "altitude_ft": round(ELEVATION + 300 + 700 * km_beyond_end),
        "vertical_rate_fpm": 2500,
        "ground_speed_kt": 170.0,
        **fields,
    }
    along = ENDS[ident].length_km + km_beyond_end
    return aircraft(icao, ident, along, cross_km, track_offset, **fields)


def rolling(ident, along_km, speed_kt, icao="4b0003", cross_km=0.0) -> Aircraft:
    """On the ground on runway *ident*, heading down the runway."""
    return aircraft(icao, ident, along_km, cross_km, on_ground=True, ground_speed_kt=speed_kt)


def monitor(**kwargs) -> RunwayMonitor:
    return RunwayMonitor(RUNWAYS, ELEVATION, **kwargs)


def detect(*aircraft, history=None) -> RunwayUse:
    return monitor().update(aircraft, T0, history)


# --- geometry ---------------------------------------------------------------------------


def test_runway_ends_follow_zurichs_runways():
    headings = {ident: round(end.heading_deg) for ident, end in ENDS.items()}
    assert headings == {"10": 96, "28": 276, "14": 137, "32": 317, "16": 155, "34": 335}
    assert ENDS["14"].length_km == pytest.approx(3.3, abs=0.05)
    assert ENDS["32"].length_km == ENDS["14"].length_km


def test_offsets_along_and_across_the_runway():
    along, cross = ENDS["28"].offsets_km(*point("28", -5.0, 0.2))
    assert along == pytest.approx(-5.0, abs=0.01)
    assert cross == pytest.approx(0.2, abs=0.01)
    assert ENDS["28"].offsets_km(ENDS["28"].lat, ENDS["28"].lon) == (0.0, 0.0)


# --- arrivals and departures ------------------------------------------------------------


def test_no_traffic_no_evidence():
    assert detect() == RunwayUse()


def test_arrivals_on_14_and_34_ranked_by_count():
    use = detect(landing("14", 15, "a1"), landing("14", 3, "a2"), landing("34", 8, "a3"))
    assert use == RunwayUse(arrivals=("14", "34"))


def test_departures_from_28_and_16_ranked_by_count():
    use = detect(
        taking_off("28", 1.5, "d1"), taking_off("16", 0.5, "d2"), taking_off("16", 4, "d3")
    )
    assert use == RunwayUse(departures=("16", "28"))


def test_typical_morning():
    use = detect(landing("14", 10, "a1"), taking_off("28", 2, "d1"), taking_off("32", 3, "d2"))
    assert use.arrivals == ("14",)
    assert set(use.departures) == {"28", "32"}


def test_classify_reports_the_runway_end():
    ends = tuple(ENDS.values())
    observation = classify(landing("34", 6), ends, ELEVATION)
    assert (observation.use, observation.ident) == ("arrival", "34")
    observation = classify(taking_off("10", 2), ends, ELEVATION)
    assert (observation.use, observation.ident) == ("departure", "10")


def test_each_aircraft_counts_once():
    m = monitor()
    for minute, km in enumerate((15, 10, 5)):  # one arrival on 34, seen three times
        m.update([landing("34", km, "a1")], T0 + minute * MINUTE)
    use = m.update([landing("14", 10, "a2"), landing("14", 5, "a3")], T0 + 3 * MINUTE)
    assert use.arrivals == ("14", "34")


def test_a_tie_goes_to_the_most_recent_use():
    m = monitor()
    m.update([landing("34", 5, "a1")], T0)
    assert m.update([landing("14", 5, "a2")], T0 + MINUTE).arrivals == ("14", "34")


def test_min_aircraft():
    m = monitor(min_aircraft=2)
    assert m.update([landing("14", 9, "a1"), taking_off("28", 1, "d1")], T0) == RunwayUse()
    assert m.update([landing("14", 9, "a2")], T0 + MINUTE) == RunwayUse(arrivals=("14",))


# --- what must not count ----------------------------------------------------------------


@pytest.mark.parametrize("offset", [20, 45, 90, -30, 180])
def test_crossing_the_centreline_at_an_angle_does_not_count(offset):
    assert detect(landing("14", 6, track_offset=offset)) == RunwayUse()
    assert detect(taking_off("28", 2, track_offset=offset)) == RunwayUse()


def test_crossing_with_history_does_not_count():
    crossing = landing("14", 6, cross_km=0.05, track_offset=40)
    history = TrackHistory()
    history.add(crossing.icao, *point("14", -8, -1.7), T0 - MINUTE)
    assert detect(crossing, history=history) == RunwayUse()


@pytest.mark.parametrize("rate", [0, -500, 1000])
def test_overflight_at_fl300_does_not_count(rate):
    over_final = landing("14", 8, altitude_ft=30_000, vertical_rate_fpm=rate)
    over_runway = aircraft("4b0009", "28", 1.0, altitude_ft=30_000, vertical_rate_fpm=rate)
    beyond_end = taking_off("16", 2, altitude_ft=30_000, vertical_rate_fpm=rate)
    assert detect(over_final, over_runway, beyond_end) == RunwayUse()


def test_go_around_is_not_a_departure():
    m = monitor()
    m.update([landing("14", 3, "ga1")], T0)
    m.update([aircraft("ga1", "14", 2.0, altitude_ft=ELEVATION + 400, vertical_rate_fpm=1500)], T0)
    use = m.update([taking_off("14", 1.5, "ga1")], T0 + MINUTE)
    assert use == RunwayUse(arrivals=("14",))


def test_turning_departure_counts_only_while_on_runway_track():
    turned = taking_off("28", 4.9, "d1", track_offset=-19, cross_km=-0.4)
    assert detect(turned) == RunwayUse()
    m = monitor()
    m.update([taking_off("28", 1.0, "d1")], T0)
    assert m.update([turned], T0 + MINUTE) == RunwayUse(departures=("28",))


def test_departure_long_after_the_runway_end_does_not_count():
    assert detect(taking_off("28", 7)) == RunwayUse()


@pytest.mark.parametrize(
    ("candidate", "why"),
    [
        (landing("14", 5, vertical_rate_fpm=1500), "climbing on the approach"),
        (landing("14", 25), "too far out"),
        (landing("14", 3, altitude_ft=ELEVATION + 4000), "far above the glide path"),
        (landing("14", 5, altitude_ft=None), "no altitude"),
        (landing("14", 5, track_deg=None), "no track"),
        (landing("14", 5, position_age_s=45), "stale position"),
        (landing("14", 3, category="A7"), "helicopter"),
        (taking_off("28", 2, vertical_rate_fpm=-800), "descending beyond the runway"),
        (taking_off("28", 2, altitude_ft=ELEVATION + 6000), "too high to have just departed"),
    ],
)
def test_ambiguous_cases_do_not_count(candidate, why):
    assert detect(candidate) == RunwayUse(), why


def test_missing_vertical_rate_is_fine_off_the_runway_but_not_over_it():
    assert detect(landing("14", 5, vertical_rate_fpm=None)).arrivals == ("14",)
    assert detect(taking_off("28", 2, vertical_rate_fpm=None)).departures == ("28",)
    over_runway = aircraft("4b0009", "28", 1.5, altitude_ft=ELEVATION + 100)
    assert detect(over_runway) == RunwayUse()


def test_take_off_roll_reported_airborne_is_not_a_landing():
    roll = aircraft("4b0009", "28", 1.0, altitude_ft=ELEVATION, vertical_rate_fpm=0)
    assert detect(roll) == RunwayUse()


def test_short_final_and_lift_off_over_the_runway():
    flare = aircraft("a1", "14", 0.3, altitude_ft=ELEVATION + 50, vertical_rate_fpm=-700)
    lift_off = aircraft("d1", "28", 2.0, altitude_ft=ELEVATION + 100, vertical_rate_fpm=1800)
    assert detect(flare, lift_off) == RunwayUse(arrivals=("14",), departures=("28",))


# --- alignment over two positions -------------------------------------------------------


def test_slightly_off_the_centreline_needs_a_confirming_earlier_position():
    arrival = landing("14", 6, cross_km=0.2)
    assert detect(arrival) == RunwayUse()
    history = TrackHistory()
    history.add(arrival.icao, *point("14", -10, 0.15), T0 - MINUTE)
    history.add(arrival.icao, arrival.lat, arrival.lon, T0)  # as after history.update()
    assert detect(arrival, history=history) == RunwayUse(arrivals=("14",))


@pytest.mark.parametrize(
    ("along", "cross", "why"),
    [(-10, 2.0, "still turning onto final"), (-3, 0.0, "ahead of the aircraft")],
)
def test_earlier_position_must_be_on_the_centreline_and_behind(along, cross, why):
    arrival = landing("14", 6, cross_km=0.2)
    history = TrackHistory()
    history.add(arrival.icao, *point("14", along, cross), T0 - MINUTE)
    assert detect(arrival, history=history) == RunwayUse(), why


def test_on_the_centreline_needs_no_history():
    assert detect(landing("14", 6, cross_km=0.05)).arrivals == ("14",)
    assert detect(landing("14", 6, cross_km=0.4)) == RunwayUse()


# --- ground roll ------------------------------------------------------------------------


def test_take_off_roll_after_taxiing():
    m = monitor()
    assert m.update([rolling("28", 0.05, 8, "d1")], T0) == RunwayUse()  # lining up
    assert m.update([rolling("28", 1.2, 120, "d1")], T0 + MINUTE / 2) == RunwayUse(
        departures=("28",)
    )


def test_landing_roll_after_being_airborne():
    m = monitor()
    m.update([landing("16", 4, "a1", track_offset=40)], T0)  # on base: no evidence yet
    assert m.update([rolling("16", 1.0, 110, "a1")], T0 + MINUTE) == RunwayUse(arrivals=("16",))


def test_ambiguous_rolls_do_not_count():
    m = monitor()
    assert m.update([rolling("34", 1.0, 120, "x1")], T0) == RunwayUse()  # first sighting
    assert m.update([rolling("34", 2.0, 60, "x1")], T0 + MINUTE / 4) == RunwayUse()  # slowing


def test_roll_beside_the_runway_or_long_after_taxiing_does_not_count():
    m = monitor()
    m.update([rolling("28", 0.0, 10, "t1"), rolling("28", 0.0, 10, "t2")], T0)
    use = m.update(
        [rolling("28", 1.0, 50, "t1", cross_km=0.25), rolling("28", 1.0, 120, "t2")],
        T0 + 4 * MINUTE,  # t2's taxiing sighting is forgotten after three minutes
    )
    assert use == RunwayUse()


# --- memory -----------------------------------------------------------------------------


def test_window_expiry():
    m = monitor()
    m.update([landing("14", 5, "a1"), taking_off("28", 2, "d1")], T0)
    assert m.use(T0 + 20 * MINUTE) == RunwayUse(arrivals=("14",), departures=("28",))
    assert m.update([], T0 + 20 * MINUTE + timedelta(seconds=1)) == RunwayUse()


def test_window_runs_from_the_last_sighting():
    m = monitor(window_s=600)
    m.update([landing("14", 12, "a1")], T0)
    m.update([landing("14", 3, "a1")], T0 + 2 * MINUTE)
    assert m.use(T0 + 12 * MINUTE).arrivals == ("14",)
    assert m.use(T0 + 13 * MINUTE) == RunwayUse()


def test_runway_change_shows_the_new_runway_first():
    m = monitor()
    m.update([landing("14", 5, "a1")], T0)
    m.update([landing("28", 5, "a2")], T0 + 10 * MINUTE)
    m.update([landing("28", 5, "a3")], T0 + 12 * MINUTE)
    assert m.use(T0 + 15 * MINUTE).arrivals == ("28", "14")
    assert m.use(T0 + 25 * MINUTE).arrivals == ("28",)


def test_tolerances_are_honoured():
    loose = monitor(tolerances=RunwayTolerances(max_track_offset_deg=30))
    assert loose.update([landing("14", 6, track_offset=20)], T0).arrivals == ("14",)


@pytest.mark.parametrize("kwargs", [{"window_s": 0}, {"min_aircraft": 0}])
def test_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        monitor(**kwargs)
