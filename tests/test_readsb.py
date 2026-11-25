import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from zurich_air.data.readsb import ReadsbError, parse_aircraft, parse_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "readsb_small.json"
FETCHED = datetime(2026, 12, 24, 14, 43, tzinfo=UTC)


def entry(**fields):
    return {"hex": "4b0001", "lat": 47.4, "lon": 8.5, **fields}


def test_fixture_parses():
    snapshot = parse_snapshot(json.loads(FIXTURE.read_text()), source="adsb.fi", fetched_at=FETCHED)
    assert snapshot.source == "adsb.fi"
    assert snapshot.total_reported == 6
    assert snapshot.timestamp == datetime(2026, 12, 24, 14, 42, tzinfo=UTC)
    assert [a.icao for a in snapshot.aircraft] == [
        "4b0001",
        "4b0002",
        "4b0003",
        "~2b0004",
        "4b0005",
        "4b0006",
    ]
    first = snapshot.aircraft[0]
    assert first.callsign == "SWR001"
    assert first.registration == "HB-AAA"
    assert first.type_code == "BCS3"
    assert first.altitude_ft == 4200
    assert first.ground_speed_kt == pytest.approx(180.5)
    assert first.track_deg == pytest.approx(137.0)
    assert first.vertical_rate_fpm == -800
    assert first.category == "A3"
    assert first.squawk == "1000"
    assert first.position_age_s == pytest.approx(0.4)
    assert not first.emergency and not first.military and not first.on_ground


def test_ground():
    a = parse_aircraft(entry(alt_baro="ground", alt_geom=1500))
    assert a.on_ground
    assert a.altitude_ft is None


def test_blank_callsign_is_none_and_label_falls_back():
    a = parse_aircraft(entry(flight="        ", r=" HB-ZZZ "))
    assert a.callsign is None
    assert a.registration == "HB-ZZZ"
    assert a.label == "HB-ZZZ"


def test_callsign_padding_stripped():
    assert parse_aircraft(entry(flight="SWR57Z  ")).callsign == "SWR57Z"


def test_only_position():
    a = parse_aircraft({"hex": "4B0001", "lat": 47.4, "lon": 8.5})
    assert a.icao == "4b0001"
    assert a.callsign is None
    assert a.altitude_ft is None
    assert a.track_deg is None
    assert a.ground_speed_kt is None
    assert a.vertical_rate_fpm is None
    assert a.position_age_s == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        {"lat": 47.4, "lon": 8.5},  # no hex
        {"hex": "", "lat": 47.4, "lon": 8.5},
        {"hex": "xyz123", "lat": 47.4, "lon": 8.5},
        {"hex": 4915201, "lat": 47.4, "lon": 8.5},
        {"hex": "4b0001"},  # no position
        {"hex": "4b0001", "lat": None, "lon": 8.5},
        {"hex": "4b0001", "lat": "47.4", "lon": "8.5"},
        {"hex": "4b0001", "lat": 91.0, "lon": 8.5},
        {"hex": "4b0001", "lat": 47.4, "lon": float("nan")},
        {"hex": "4b0001", "lat": True, "lon": 8.5},
        "not an object",
        None,
    ],
)
def test_entries_without_identity_or_position_are_dropped(bad):
    assert parse_aircraft(bad) is None


def test_garbage_values_become_none():
    a = parse_aircraft(
        entry(
            flight=42,
            r=["HB-AAA"],
            t={"x": 1},
            alt_baro="high",
            gs=-5,
            track="north",
            true_heading=None,
            baro_rate=float("inf"),
            geom_rate="fast",
            category=7,
            squawk="77",
            emergency=3,
            dbFlags="military",
            seen_pos=-1,
            seen="now",
        )
    )
    assert a is not None
    assert a.callsign is None and a.registration is None and a.type_code is None
    assert a.altitude_ft is None and not a.on_ground
    assert a.ground_speed_kt is None
    assert a.track_deg is None
    assert a.vertical_rate_fpm is None
    assert a.category is None and a.squawk is None
    assert not a.emergency and not a.military
    assert a.position_age_s == 0.0


def test_non_icao_address_keeps_tilde():
    a = parse_aircraft(entry(hex="~2B0004"))
    assert a.icao == "~2b0004"
    assert a.label == "~2B0004"


def test_track_falls_back_to_true_heading_and_is_normalised():
    assert parse_aircraft(entry(true_heading=210.0)).track_deg == pytest.approx(210.0)
    assert parse_aircraft(entry(track=95.0, true_heading=210.0)).track_deg == pytest.approx(95.0)
    assert parse_aircraft(entry(track=370.0)).track_deg == pytest.approx(10.0)


def test_vertical_rate_prefers_baro_then_geom():
    assert parse_aircraft(entry(baro_rate=-640, geom_rate=-700)).vertical_rate_fpm == -640
    assert parse_aircraft(entry(geom_rate=128.4)).vertical_rate_fpm == 128


def test_altitude_rounded_to_int():
    assert parse_aircraft(entry(alt_baro=12345.6)).altitude_ft == 12346


@pytest.mark.parametrize("squawk", ["7500", "7600", "7700", 7700])
def test_emergency_squawk(squawk):
    a = parse_aircraft(entry(squawk=squawk))
    assert a.emergency
    assert a.squawk == str(squawk)


@pytest.mark.parametrize(("value", "expected"), [("none", False), ("general", True), (None, False)])
def test_emergency_field(value, expected):
    assert parse_aircraft(entry(emergency=value, squawk="1000")).emergency is expected


@pytest.mark.parametrize(("flags", "military"), [(1, True), (3, True), (2, False), (8, False)])
def test_military_flag(flags, military):
    assert parse_aircraft(entry(dbFlags=flags)).military is military


def test_position_age_falls_back_to_seen():
    assert parse_aircraft(entry(seen=4.5)).position_age_s == pytest.approx(4.5)
    assert parse_aircraft(entry(seen_pos=12.0, seen=1.0)).position_age_s == pytest.approx(12.0)


def test_missing_now_uses_fetch_time():
    snapshot = parse_snapshot({"ac": []}, source="x", fetched_at=FETCHED)
    assert snapshot.timestamp == FETCHED
    assert snapshot.aircraft == ()
    assert snapshot.total_reported == 0


@pytest.mark.parametrize("now", ["soon", None, -1, float("nan"), 1e30])
def test_bad_now_uses_fetch_time(now):
    snapshot = parse_snapshot({"now": now, "ac": []}, source="x", fetched_at=FETCHED)
    assert snapshot.timestamp == FETCHED


def test_now_in_seconds_is_accepted():
    # A local readsb writes "now" in seconds and lists aircraft under "aircraft".
    payload = {"now": 1798123320.5, "aircraft": [entry()]}
    snapshot = parse_snapshot(payload, source="readsb", fetched_at=FETCHED)
    assert snapshot.timestamp == datetime(2026, 12, 24, 14, 42, 0, 500000, tzinfo=UTC)
    assert snapshot.timestamp.tzinfo is not None
    assert len(snapshot.aircraft) == 1


def test_bad_entries_are_skipped_not_fatal():
    payload = {"now": 1798123320000, "total": 3, "ac": [entry(), "junk", {"hex": "4b0002"}]}
    snapshot = parse_snapshot(payload, source="x", fetched_at=FETCHED)
    assert [a.icao for a in snapshot.aircraft] == ["4b0001"]
    assert snapshot.total_reported == 3


@pytest.mark.parametrize("payload", [[], "text", None, {"msg": "error"}, {"ac": {"a": 1}}])
def test_not_a_readsb_response(payload):
    with pytest.raises(ReadsbError):
        parse_snapshot(payload, source="x", fetched_at=FETCHED)
