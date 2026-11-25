import pytest

from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import (
    AIRCRAFT_TYPES,
    HELICOPTER_TYPES,
    WIDEBODY_TYPES,
    is_helicopter,
    operator_name,
    type_name,
)


@pytest.mark.parametrize(
    ("callsign", "expected"),
    [
        ("SWR123", "Swiss"),
        ("swr12x", "Swiss"),
        ("UAE87", "Emirates"),
        ("SAZ17", "Rega"),
        ("HBJXA", None),  # registration used as callsign
        ("XAX123", None),  # unknown prefix
        ("SWR", None),
        ("", None),
        (None, None),
    ],
)
def test_operator_name(callsign, expected):
    assert operator_name(callsign) == expected


def test_type_name_known_unknown_and_missing():
    assert type_name("B77W") == "777-300ER"
    assert type_name("a20n") == "A320neo"
    assert type_name("ZZZZ") == "ZZZZ"  # unknown codes are shown as-is, never guessed
    assert type_name(None) is None
    assert type_name("") is None


def test_type_sets_are_described():
    assert WIDEBODY_TYPES.issubset(AIRCRAFT_TYPES)
    assert HELICOPTER_TYPES.issubset(AIRCRAFT_TYPES)
    assert WIDEBODY_TYPES.isdisjoint(HELICOPTER_TYPES)


def test_is_helicopter():
    assert is_helicopter(Aircraft("4b0001", 47.0, 8.0, type_code="EC45"))
    assert is_helicopter(Aircraft("4b0001", 47.0, 8.0, category="A7"))
    assert not is_helicopter(Aircraft("4b0001", 47.0, 8.0, type_code="A320"))
    assert not is_helicopter(Aircraft("4b0001", 47.0, 8.0))


@pytest.mark.parametrize(
    ("callsign", "registration", "expected"),
    [("SWR12", "HB-JNA", "SWR12"), ("  ", "HB-JNA", "HB-JNA"), (None, "", "4B1A2F")],
)
def test_label_fallback(callsign, registration, expected):
    aircraft = Aircraft("4b1a2f", 47.0, 8.0, callsign=callsign, registration=registration)
    assert aircraft.label == expected
