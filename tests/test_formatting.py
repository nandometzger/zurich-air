from datetime import UTC, datetime

import pytest

from zurich_air.data.aircraft import Aircraft
from zurich_air.rendering import formatting
from zurich_air.rendering.fonts import ELLIPSIS, font, text_width, truncate


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


def test_clock_uses_local_time():
    moment = datetime(2026, 12, 24, 14, 42, tzinfo=UTC)
    assert formatting.clock(moment, "Europe/Zurich") == "15:42"


def test_distance():
    assert formatting.distance(80.0) == "80 KM"
    assert formatting.distance(37.5) == "37.5 KM"


def test_truncate_fits_and_marks_cut():
    face = font(10)
    text = "Lufthansa City · CRJ900"
    assert truncate(text, face, 500) == text
    short = truncate(text, face, 60)
    assert short.endswith(ELLIPSIS)
    assert text_width(short, face) <= 60
    assert truncate(text, face, 1) == ""


def test_tracked_text_is_wider():
    face = font(10)
    assert text_width("AIRCRAFT", face, tracking=1) > text_width("AIRCRAFT", face)
    assert text_width("", face) == 0
