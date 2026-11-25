import pytest

from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import (
    AIRCRAFT_TYPES,
    HELICOPTER_TYPES,
    NOTABLE_TYPES,
    WIDEBODY_TYPES,
    is_helicopter,
    is_rescue,
    operator_name,
    type_family,
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
    assert NOTABLE_TYPES.issubset(AIRCRAFT_TYPES)
    assert WIDEBODY_TYPES.isdisjoint(HELICOPTER_TYPES)


def test_is_rescue():
    assert is_rescue(Aircraft("4b0001", 47.0, 8.0, callsign="SAZ12"))
    assert is_rescue(Aircraft("4b0001", 47.0, 8.0, callsign="saz7"))
    assert not is_rescue(Aircraft("4b0001", 47.0, 8.0, callsign="SAZ"))
    assert not is_rescue(Aircraft("4b0001", 47.0, 8.0, callsign="SWR12"))
    assert not is_rescue(Aircraft("4b0001", 47.0, 8.0))


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


@pytest.mark.parametrize(
    ("type_code", "family"),
    [
        ("BCS3", "A220"),  # A220-300
        ("B77W", "777"),  # 777-300ER
        ("B38M", "737 MAX"),  # 737 MAX 8
        ("A35K", "A350"),  # A350-1000
        ("B748", "747"),  # 747-8
        ("AT76", "ATR 72"),  # ATR 72-600
        ("DH8D", "Dash 8"),  # Dash 8-400
        ("C68A", "Citation"),  # Citation Latitude
        ("CL35", "Challenger"),
        ("E295", "E195"),  # E195-E2
        ("A20N", "A320neo"),  # no shorter family
        ("A388", "A380"),
        ("MD11", "MD-11"),  # the hyphen belongs to the name
        ("C17", "C-17"),
        ("AT72", "ATR 72"),
        ("b77w", "777"),
        ("ZZZZ", None),  # unknown codes have no family
        (None, None),
        ("", None),
    ],
)
def test_type_family(type_code, family):
    assert type_family(type_code) == family


def test_every_family_is_a_shorter_prefix_of_its_model():
    for code, model in AIRCRAFT_TYPES.items():
        family = type_family(code)
        assert family is not None and model.startswith(family), code
        assert family == model or (len(family) < len(model) and family[-1] != " "), code
