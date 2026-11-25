import dataclasses
import random

import pytest

from zurich_air.config import Config, RankingWeights
from zurich_air.data import synthetic
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing import ranking
from zurich_air.processing.filtering import visible_aircraft
from zurich_air.processing.geo import destination
from zurich_air.processing.ranking import (
    EMERGENCY_POINTS,
    evaluate,
    facts,
    keep_rows,
    rank,
    select_featured,
)

CONFIG = Config()
AIRPORT = (CONFIG.airport.latitude, CONFIG.airport.longitude)
CENTRE = (CONFIG.location.latitude, CONFIG.location.longitude)


def at(bearing, dist_km, ref=AIRPORT, **kwargs):
    lat, lon = destination(*ref, bearing, dist_km)
    kwargs.setdefault("callsign", "SWR1")
    return Aircraft(icao=kwargs.pop("icao", "4b0001"), lat=lat, lon=lon, **kwargs)


def arrival(dist_km, icao="4b0001", **kwargs):
    """Descending on the ILS 14 from the north-west."""
    kwargs.setdefault("altitude_ft", 1500 + round(dist_km * 320))
    return at(317, dist_km, icao=icao, track_deg=137, vertical_rate_fpm=-800, **kwargs)


def cruise(bearing, dist_km, icao, altitude_ft=37_000, **kwargs):
    return at(bearing, dist_km, ref=CENTRE, icao=icao, altitude_ft=altitude_ft, **kwargs)


def with_weights(**weights):
    ranking_config = dataclasses.replace(
        CONFIG.ranking, weights=dataclasses.replace(CONFIG.ranking.weights, **weights)
    )
    return dataclasses.replace(CONFIG, ranking=ranking_config)


def reason(component, aircraft, config=CONFIG, featured_before=False):
    return component(facts(aircraft, config, featured_before), config)


def icaos(aircraft):
    return [a.icao for a in aircraft]


# --- components ---------------------------------------------------------------------------

W = RankingWeights()


def test_emergency_component():
    assert reason(ranking.emergency, arrival(10)) is None
    r = reason(ranking.emergency, arrival(10, squawk="7700", emergency=True))
    assert (r.text, r.points) == ("emergency 7700", EMERGENCY_POINTS)
    assert reason(ranking.emergency, arrival(10, emergency=True)).text == "emergency"
    status_only = arrival(10, squawk="1000", emergency=True)  # emergency status, normal squawk
    assert reason(ranking.emergency, status_only).text == "emergency"


def test_phase_component_rewards_closeness_to_the_airport():
    near, far = (
        reason(ranking.phase, arrival(5)),
        reason(ranking.phase, arrival(35, altitude_ft=9000)),
    )
    assert near.text == "arrival ZRH 5 km"
    assert W.arrival / 2 < far.points < near.points <= W.arrival
    departure = at(172, 10, track_deg=172, altitude_ft=6000, vertical_rate_fpm=2500)
    assert reason(ranking.phase, departure).text == "departure ZRH 10 km"
    assert reason(ranking.phase, cruise(0, 10, "a")) is None


@pytest.mark.parametrize(
    ("type_code", "expected"),
    [
        ("A388", ("notable A388", W.notable_type)),
        ("a124", ("notable A124", W.notable_type)),
        ("B77W", ("widebody B77W", W.widebody)),
        ("A320", None),
        (None, None),
    ],
)
def test_type_component(type_code, expected):
    r = reason(ranking.notable_type, cruise(0, 10, "a", type_code=type_code))
    assert (r and (r.text, r.points)) == expected


def test_helicopter_rescue_military_and_interesting_components():
    plain = cruise(0, 10, "a")
    assert all(
        reason(c, plain) is None
        for c in (ranking.helicopter, ranking.rescue, ranking.military, ranking.interesting)
    )
    rega = cruise(0, 10, "a", callsign="SAZ12", type_code="EC45")
    assert reason(ranking.helicopter, rega).points == W.helicopter
    assert reason(ranking.rescue, rega).points == W.rescue
    assert reason(ranking.military, cruise(0, 10, "a", military=True)).points == W.military
    flagged = cruise(0, 10, "a", interesting=True)
    assert reason(ranking.interesting, flagged).points == W.interesting


def test_low_altitude_component():
    points = [reason(ranking.low_altitude, cruise(0, 10, "a", altitude_ft=alt)).points for alt in
              (-200, 0, 5000, 39_000)]  # fmt: skip
    assert points[0] == points[1] == W.low_altitude
    assert points[1] > points[2] > points[3] > 0
    assert reason(ranking.low_altitude, cruise(0, 10, "a", altitude_ft=45_000)).points == 0
    assert reason(ranking.low_altitude, cruise(0, 10, "a", altitude_ft=None)) is None


def test_near_centre_component():
    assert reason(ranking.near_centre, cruise(0, 0, "a")).points == pytest.approx(W.near_centre)
    half = reason(ranking.near_centre, cruise(0, CONFIG.radar.range_km / 2, "a"))
    assert half.points == pytest.approx(W.near_centre / 2, abs=0.01)
    assert reason(ranking.near_centre, cruise(0, 90, "a")).points == 0


def test_no_identity_component():
    assert reason(ranking.no_identity, cruise(0, 10, "a")) is None
    assert reason(ranking.no_identity, cruise(0, 10, "a", callsign=None, type_code="A320")) is None
    anonymous = cruise(0, 10, "a", callsign=None, registration="HB-JXC")
    assert reason(ranking.no_identity, anonymous).points == W.no_identity < 0


def test_featured_before_component():
    assert reason(ranking.featured_before, arrival(10)) is None
    assert reason(ranking.featured_before, arrival(10), featured_before=True).points == (
        W.featured_before
    )


def test_score_is_the_sum_of_the_reasons():
    candidate = evaluate(arrival(9, type_code="A388"), CONFIG)
    assert candidate.phase == "arrival"
    assert candidate.score == pytest.approx(sum(r.points for r in candidate.reasons))
    texts = [r.text for r in candidate.reasons]
    assert texts[:2] == ["arrival ZRH 9 km", "notable A388"]
    assert str(candidate.reasons[1]) == "notable A388 +30"


def test_zero_weights_switch_a_component_off():
    config = with_weights(notable_type=0)
    assert "notable A388" not in [
        r.text for r in evaluate(arrival(9, type_code="A388"), config).reasons
    ]


def test_missing_fields_do_not_crash():
    candidate = evaluate(Aircraft("4b0001", *AIRPORT), CONFIG)
    assert candidate.phase is None
    assert [r.text for r in candidate.reasons][-1] == "no callsign or type"


# --- selection ----------------------------------------------------------------------------


def test_emergency_always_first():
    emergency = cruise(180, 75, "e", altitude_ft=38_000, emergency=True, squawk="7600")
    others = [arrival(3 + 4 * i, icao=f"x{i}", type_code="A388") for i in range(6)]
    featured = select_featured([*others, emergency], CONFIG)
    assert featured[0] is emergency
    # Even with every weight at its maximum and the emergency bonus off the table.
    loud = with_weights(**{f.name: 100 for f in dataclasses.fields(RankingWeights)})
    assert select_featured([*others, emergency], loud)[0] is emergency


def test_privacy_aircraft_are_never_featured_but_stay_on_the_radar():
    ladd = arrival(3, icao="ladd", privacy=True, type_code="A388")
    other = cruise(0, 40, "other")
    assert select_featured([ladd, other], CONFIG) == [other]
    assert ladd in visible_aircraft([ladd, other], CONFIG)


def test_ground_traffic_skipped():
    parked = at(0, 0.5, icao="ground", on_ground=True)
    on_pad = at(0, 20, icao="pad", altitude_ft=1350, ground_speed_kt=0.7, category="A7")
    flying = cruise(0, 40, "air")
    assert select_featured([parked, on_pad, flying], CONFIG) == [flying]


def test_count_and_order():
    traffic = [arrival(3 + 4 * i, icao=f"{i:06x}") for i in range(8)]
    featured = select_featured(traffic, CONFIG)
    assert featured == traffic[: CONFIG.aircraft.featured]  # closest arrival first
    assert select_featured(traffic, CONFIG, n=2) == traffic[:2]
    assert select_featured([], CONFIG) == []
    assert len(rank(traffic, CONFIG)) == 8


def test_deterministic_with_ties_broken_by_icao():
    twins = [cruise(0, 30, icao, type_code="A320") for icao in ("4b0003", "4b0001", "4b0002")]
    shuffled = twins[:]
    random.Random(1).shuffle(shuffled)
    assert icaos(select_featured(twins, CONFIG)) == ["4b0001", "4b0002", "4b0003"]
    assert select_featured(shuffled, CONFIG) == select_featured(twins, CONFIG)


def test_departure_through_11000_ft_beats_high_overflights():
    # Regression from real evening traffic: two overflights at FL390 / FL410 (one a
    # widebody) used to outrank a departure climbing through 11 000 ft near ZRH.
    departure = at(200, 15, icao="dep", track_deg=200, altitude_ft=11_000, vertical_rate_fpm=2000)
    fl390 = at(90, 6.5, icao="fl390", altitude_ft=39_000, track_deg=239, type_code="A20N")
    fl410 = at(225, 47, icao="fl410", altitude_ft=41_000, track_deg=123, type_code="A359")
    assert icaos(select_featured([fl390, fl410, departure], CONFIG, n=1)) == ["dep"]
    # The real aircraft was descending away from the airport on a downwind leg: no
    # phase is inferred, but being low still puts it above both overflights.
    downwind = dataclasses.replace(departure, track_deg=155, vertical_rate_fpm=-768)
    assert evaluate(downwind, CONFIG).phase is None
    assert icaos(select_featured([fl390, fl410, downwind], CONFIG, n=1)) == ["dep"]


def test_typical_scene_features_terminal_traffic_and_the_rega_helicopter():
    featured = select_featured(synthetic.scene("typical").snapshot.aircraft, CONFIG)
    assert all(a.altitude_ft is not None and a.altitude_ft < 10_000 for a in featured)
    assert featured[0].type_code == "A388"
    assert "SAZ020" in [a.callsign for a in featured]


# --- calm list ----------------------------------------------------------------------------


def five_incumbents():
    return [arrival(4 + 3 * i, icao=f"in{i}") for i in range(5)]


def test_previous_selection_kept_unless_clearly_beaten():
    incumbents = five_incumbents()
    weakest = evaluate(incumbents[-1], CONFIG).score
    previous = icaos(incumbents)

    slightly_better = cruise(0, 1, "new", altitude_ft=1500, type_code="A388")
    assert weakest < evaluate(slightly_better, CONFIG).score < weakest + W.featured_before
    fresh_pick = icaos(select_featured([*incumbents, slightly_better], CONFIG))
    assert "new" in fresh_pick and "in4" not in fresh_pick
    assert icaos(select_featured([*incumbents, slightly_better], CONFIG, previous=previous)) == (
        previous
    )

    clearly_better = dataclasses.replace(slightly_better, military=True)
    featured = select_featured([*incumbents, clearly_better], CONFIG, previous=previous)
    assert "new" in icaos(featured)
    assert "in4" not in icaos(featured)


def test_rows_keep_their_place():
    incumbents = five_incumbents()
    previous = list(reversed(icaos(incumbents)))  # last frame, the order was the other way
    assert icaos(select_featured(incumbents, CONFIG, previous=previous)) == previous


def test_newcomer_slots_in_above_the_first_row_it_outscores():
    a, b, c = (rank([arrival(d, icao=i)], CONFIG)[0] for d, i in ((5, "a"), (20, "b"), (35, "c")))
    newcomer = rank([arrival(10, icao="new")], CONFIG)[0]
    rows = keep_rows([a, newcomer, b, c], previous=["c", "a", "b"])
    assert [r.aircraft.icao for r in rows] == ["new", "c", "a", "b"]
    rows = keep_rows([a, newcomer, b, c], previous=["a", "b", "c"])
    assert [r.aircraft.icao for r in rows] == ["a", "new", "b", "c"]


def test_emergency_moves_to_the_top_of_a_calm_list():
    incumbents = five_incumbents()
    incumbents[3] = dataclasses.replace(incumbents[3], emergency=True, squawk="7700")
    featured = select_featured(incumbents, CONFIG, previous=icaos(incumbents))
    assert icaos(featured) == ["in3", "in0", "in1", "in2", "in4"]


def test_selection_is_stable_when_repeated():
    traffic = synthetic.scene("busy").snapshot.aircraft
    first = icaos(select_featured(traffic, CONFIG))
    assert icaos(select_featured(traffic, CONFIG, previous=first)) == first
