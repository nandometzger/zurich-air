from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from zurich_air.schedule import (
    QuietHours,
    clock_is_plausible,
    next_delay,
    seconds_until_next_tick,
)

ZRH = ZoneInfo("Europe/Zurich")
NIGHT = QuietHours(time(23, 30), time(6, 0))


def at(hour, minute, second=0, day=15):
    return datetime(2026, 12, day, hour, minute, second, tzinfo=ZRH)


@pytest.mark.parametrize(
    ("now", "delay"),
    [
        (1.0, 60.0),  # ticks land 1 s past each whole minute
        (10.5, 50.5),
        (58.5, 2.5),
        (59.5, 61.5),  # too close to the next slot: skip it rather than burst
        (60.9, 60.1),
    ],
)
def test_ticks_align_to_interval(now, delay):
    assert seconds_until_next_tick(now, 60.0) == pytest.approx(delay)


@pytest.mark.parametrize(
    ("text", "window"),
    [
        ("23:30-06:00", NIGHT),
        (" 23:30 - 06:00 ", NIGHT),
        ("0:00-6:30", QuietHours(time(0, 0), time(6, 30))),
        ("12:00-14:00", QuietHours(time(12, 0), time(14, 0))),
    ],
)
def test_parse_quiet_hours(text, window):
    assert QuietHours.parse(text) == window


@pytest.mark.parametrize("text", ["", "  "])
def test_empty_quiet_hours_disable_them(text):
    assert QuietHours.parse(text) is None


@pytest.mark.parametrize(
    "text",
    ["23:30", "23:30-", "23:30-06:00-07:00", "11pm-6am", "24:00-06:00", "23:60-06:00", "6-7",
     "23:30-23:30"],
)  # fmt: skip
def test_invalid_quiet_hours_rejected(text):
    with pytest.raises(ValueError):
        QuietHours.parse(text)


def test_quiet_hours_round_trip_as_text():
    assert str(NIGHT) == "23:30-06:00"


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (at(23, 29, 59), False),
        (at(23, 30), True),  # start included
        (at(0, 0), True),
        (at(3, 0), True),
        (at(5, 59, 59), True),
        (at(6, 0), False),  # end excluded
        (at(12, 0), False),
    ],
)
def test_window_across_midnight(moment, inside):
    assert NIGHT.contains(moment) is inside


@pytest.mark.parametrize(
    ("moment", "inside"), [(at(11, 59), False), (at(12, 0), True), (at(14, 0), False)]
)
def test_window_within_a_day(moment, inside):
    assert QuietHours.parse("12:00-14:00").contains(moment) is inside


def test_seconds_left_until_the_window_ends():
    assert NIGHT.seconds_left(at(23, 45)) == 6 * 3600 + 15 * 60  # ends tomorrow
    assert NIGHT.seconds_left(at(2, 0)) == 4 * 3600
    assert NIGHT.seconds_left(at(12, 0)) == 0


def test_seconds_left_counts_real_time_across_a_clock_change():
    # 25 October 2026: clocks go back from 03:00 to 02:00, so the night is an hour longer.
    night = QuietHours.parse("00:00-06:00")
    assert night.seconds_left(datetime(2026, 10, 25, 1, 0, tzinfo=ZRH)) == 6 * 3600


@pytest.mark.parametrize(
    ("now", "delay"),
    [
        (at(23, 28, 1), 60),  # before quiet hours: every minute
        (at(23, 29, 1), 60),  # the next tick, 23:30:01, is the first quiet one
        (at(23, 30, 1), 600),  # quiet: every ten minutes
        (at(2, 0, 1), 600),
        (at(5, 50, 1), 600),  # lands on 06:00:01, the first normal tick
        (at(6, 0, 1), 60),
        (at(12, 0, 1), 60),
    ],
)
def test_interval_follows_quiet_hours(now, delay):
    assert next_delay(now, 60, NIGHT, 600) == pytest.approx(delay)


def test_first_tick_after_quiet_hours_is_on_time():
    window = QuietHours.parse("23:35-06:05")  # not on the 10-minute grid
    assert next_delay(at(5, 50, 1), 60, window, 600) == pytest.approx(600)  # 06:00:01
    assert next_delay(at(6, 0, 1), 60, window, 600) == pytest.approx(300)  # 06:05:01
    assert next_delay(at(6, 4, 59), 60, window, 600) == pytest.approx(2)  # never a burst


def test_no_quiet_hours():
    assert next_delay(at(2, 0, 1), 60, None, 600) == pytest.approx(60)


def test_clock_plausibility():
    assert clock_is_plausible(datetime(2026, 11, 25, tzinfo=UTC))
    assert not clock_is_plausible(datetime(2025, 12, 31, 23, 59, tzinfo=UTC))
    assert not clock_is_plausible(datetime(1970, 1, 1, tzinfo=UTC))
