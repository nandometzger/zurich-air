import itertools
from datetime import UTC, datetime

import pytest

from zurich_air.data.aircraft import Aircraft
from zurich_air.data.metar import Weather
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.processing.runways import RunwayUse
from zurich_air.rendering import formatting
from zurich_air.rendering.fonts import fit_parts, font, text_width


@pytest.mark.parametrize(
    ("feet", "expected"),
    [
        (34_000, "FL340"),
        (34_480, "FL345"),
        (7_000, "FL070"),
        (6_960, "FL070"),
        (6_500, "6500 FT"),
        (6_449, "6400 FT"),
        (1_416, "1400 FT"),
        (0, "0 FT"),
        (None, None),
    ],
)
def test_altitude_aviation(feet, expected):
    assert formatting.altitude(feet) == expected


def test_altitude_metric_and_ground():
    assert formatting.altitude(35_000, "metric") == "10 670 M"
    assert formatting.altitude(2_000, "metric") == "610 M"
    assert formatting.altitude(1_400, on_ground=True) == "GND"
    assert formatting.altitude(None, on_ground=True) == "GND"


@pytest.mark.parametrize(
    ("feet", "units", "on_ground", "expected"),
    [
        (6_500, "aviation", False, ["6500 FT", "6500"]),
        (0, "aviation", False, ["0 FT", "0"]),
        (34_000, "aviation", False, ["FL340"]),  # nothing to give
        (1_400, "aviation", True, ["GND"]),
        (34_000, "metric", False, ["10 360 M", "10 360", "10360"]),
        (2_000, "metric", False, ["610 M", "610"]),
        (None, "aviation", False, []),
    ],
)
def test_altitudes_give_way_but_keep_the_number(feet, units, on_ground, expected):
    forms = formatting.altitudes(feet, units, on_ground=on_ground)
    assert forms == expected
    if forms:
        assert forms[0] == formatting.altitude(feet, units, on_ground=on_ground)
    widths = [text_width(form, font(10)) for form in forms]
    assert all(wider > narrower for wider, narrower in itertools.pairwise(widths))


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(1500, "climb"), (300, "climb"), (299, "level"), (0, "level"), (-299, "level"),
     (-300, "descend"), (-2500, "descend"), (None, None)],
)  # fmt: skip
def test_vertical_trend(rate, expected):
    assert formatting.vertical_trend(rate) == expected


def test_description():
    swiss = Aircraft("4b0001", 47.0, 8.0, callsign="SWR8", type_code="BCS3")
    assert formatting.description(swiss) == "Swiss · A220-300"
    assert formatting.description(Aircraft("4b0001", 47.0, 8.0, callsign="SWR8")) == "Swiss"
    private = Aircraft("4b0001", 47.0, 8.0, callsign="HBJXA", registration="HB-JXA")
    assert formatting.description(private) == ""  # registration is already the label
    no_callsign = Aircraft("4b0001", 47.0, 8.0, registration="HB-JXA", type_code="C172")
    assert formatting.description(no_callsign) == "Cessna 172"
    assert formatting.description(Aircraft("4b0001", 47.0, 8.0)) == ""


@pytest.mark.parametrize(
    ("callsign", "type_code", "expected"),
    [
        ("BTI6RT", "BCS3", ["airBaltic · A220-300", "airBaltic · A220", "A220-300", "A220"]),
        ("SWR8", "B38M", ["Swiss · 737 MAX 8", "Swiss · 737 MAX", "737 MAX 8", "737 MAX"]),
        ("UAE87", "A388", ["Emirates · A380", "A380"]),  # no shorter family
        ("SWR8", None, ["Swiss"]),
        ("ZZZ3PR", "A320", ["A320"]),  # unknown operator
        ("XAX5", "ZZZZ", ["ZZZZ"]),  # unknown type: the raw code, no family
    ],
)
def test_descriptions_shorten_by_meaning(callsign, type_code, expected):
    aircraft = Aircraft("4b0001", 47.0, 8.0, callsign=callsign, type_code=type_code)
    assert formatting.descriptions(aircraft) == expected
    assert formatting.description(aircraft) == expected[0]


def test_descriptions_fall_back_to_the_registration():
    unknown = Aircraft("4b0001", 47.0, 8.0, callsign="XAX5", registration="HB-JXA")
    assert formatting.descriptions(unknown) == ["HB-JXA"]
    no_callsign = Aircraft("4b0001", 47.0, 8.0, registration="HB-JXA")
    assert formatting.descriptions(no_callsign) == []  # the label already shows it
    assert formatting.descriptions(Aircraft("4b0001", 47.0, 8.0)) == []


def test_clock_uses_local_time():
    moment = datetime(2026, 12, 24, 14, 42, tzinfo=UTC)
    assert formatting.clock(moment, "Europe/Zurich") == "15:42"


def test_distance():
    assert formatting.distance(80.0) == "80 KM"
    assert formatting.distance(37.5) == "37.5 KM"


def test_tracked_text_is_wider():
    face = font(10)
    assert text_width("AIRCRAFT", face, tracking=1) > text_width("AIRCRAFT", face)
    assert text_width("", face) == 0


# --- the airport line ---------------------------------------------------------------------

MINUS = formatting.MINUS  # U+2212


def weather(direction=270, speed=8, gust=None, temp=4, qnh=1018, variable=False) -> Weather:
    observed = datetime(2026, 12, 24, 14, 20, tzinfo=UTC)
    return Weather("LSZH", observed, direction, variable, speed, gust, temp, None, qnh, "")


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (weather(270, 8, 18), "270° 8 KT G18"),
        (weather(270, 8), "270° 8 KT"),
        (weather(10, 5), "010° 5 KT"),  # three digits, as in the METAR
        (weather(360, 12), "360° 12 KT"),
        (weather(None, 12, variable=True), "VRB 12 KT"),
        (weather(None, 12, 25, variable=True), "VRB 12 KT G25"),
        (weather(None, 0), "CALM"),
        (weather(None, 7), "7 KT"),  # direction not reported: never guessed
        (None, None),
    ],
)
def test_wind(report, expected):
    assert formatting.wind(report) == expected


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(4, "4 °C"), (0, "0 °C"), (-3, f"{MINUS}3 °C"), (-12, f"{MINUS}12 °C"), (31, "31 °C")],
)
def test_temperature_uses_a_real_minus_sign(degrees, expected):
    assert formatting.temperature(weather(temp=degrees)) == expected


def test_minus_is_the_minus_sign_not_a_hyphen():
    assert formatting.MINUS == "\N{MINUS SIGN}"


def test_missing_temperature_and_qnh():
    assert formatting.temperature(weather(temp=None)) is None
    assert formatting.temperature(None) is None
    assert formatting.qnh(weather(qnh=None)) is None
    assert formatting.qnh(None) is None
    assert formatting.qnh(weather(qnh=998)) == "QNH 998"


@pytest.mark.parametrize(
    ("use", "expected"),
    [
        (RunwayUse(("14",), ("28",)), ["LDG 14", "DEP 28"]),
        (RunwayUse(("14", "16"), ("28", "32")), ["LDG 14 16", "DEP 28 32"]),
        (RunwayUse(("34",), ()), ["LDG 34"]),
        (RunwayUse((), ("16",)), ["DEP 16"]),
        (RunwayUse(), []),
        (None, []),
    ],
)
def test_runways(use, expected):
    assert formatting.runways(use) == expected


def test_airport_parts_in_order_of_importance():
    parts = formatting.airport_parts(weather(270, 8, 18), RunwayUse(("14",), ("28",)))
    assert parts == ["LDG 14", "DEP 28", "270° 8 KT G18", "4 °C", "QNH 1018"]
    assert formatting.airport_parts(weather(temp=None), None) == ["270° 8 KT", "QNH 1018"]
    assert formatting.airport_parts(None, RunwayUse((), ("28",))) == ["DEP 28"]
    assert formatting.airport_parts(None, None) == []


def test_fit_parts_drops_the_least_important_first():
    small = font(10)
    parts = ["LDG 14 16", "DEP 28 32", "VRB 12 KT G25", f"{MINUS}12 °C", "QNH 1033"]
    full = " · ".join(parts)
    width = text_width(full, small, 1)
    assert fit_parts(parts, small, width, 1) == full
    assert fit_parts(parts, small, width - 1, 1) == " · ".join(parts[:4])
    no_temperature = text_width(" · ".join(parts[:3]), small, 1)
    assert fit_parts(parts, small, no_temperature, 1) == " · ".join(parts[:3])
    assert fit_parts(parts, small, text_width("LDG 14 16", small, 1), 1) == "LDG 14 16"
    assert fit_parts(parts, small, 10, 1) == ""  # not even the runways: nothing at all
    assert fit_parts([], small, 400) == ""
    assert fit_parts(["", "CALM", ""], small, 400) == "CALM"


# --- routes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        (VerifiedRoute("OMDB", "LSZH", "arrival"), "DXB →"),
        (VerifiedRoute("LSZH", "KJFK", "departure"), "→ JFK"),
        (VerifiedRoute("EGLL", "LTFM", "overflight"), "LHR → IST"),
        (VerifiedRoute("LSZH", "DGAH", "departure"), "→ DGAH"),  # no IATA code: ICAO
        (VerifiedRoute("ZZZZ", "LSZH", "arrival"), "ZZZZ →"),  # not bundled: as given
    ],
)
def test_route_label(route, expected):
    assert formatting.route_label(route) == expected


def test_airport_code_prefers_iata():
    assert formatting.airport_code("LSZH") == "ZRH"
    assert formatting.airport_code("lsgg") == "GVA"
    assert formatting.airport_code("DGAH") == "DGAH"
