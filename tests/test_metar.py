import io
import json
import logging
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zurich_air.data.metar import MetarSource, Weather, parse_metar_json
from zurich_air.data.provider import USER_AGENT

FIXTURES = Path(__file__).parent / "fixtures" / "metar"
NORMAL_OBSERVED = datetime(2026, 11, 26, 7, 20, tzinfo=UTC)


def fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def body(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeResponse(io.BytesIO):
    status = 200


class FakeOpener:
    """Stands in for urllib.request.urlopen: returns or raises the queued outcomes."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "error", None, None)


def source(opener, clock=None, now=NORMAL_OBSERVED + timedelta(minutes=10), **kwargs):
    return MetarSource(
        "lszh", opener=opener, clock=clock or Clock(), wall_clock=lambda: now, **kwargs
    )


# --- parsing ----------------------------------------------------------------------------


def test_normal_observation():
    weather = parse_metar_json(fixture("normal"), "LSZH")
    assert weather == Weather(
        station="LSZH",
        observed_at=NORMAL_OBSERVED,
        wind_dir_deg=10,
        wind_variable=False,
        wind_speed_kt=5,
        gust_kt=None,
        temperature_c=0,
        dewpoint_c=-3,
        qnh_hpa=1012,
        raw="METAR LSZH 260720Z 01005KT 330V040 9999 FEW011 SCT023 BKN075 M00/M03 Q1012 TEMPO -SN",
    )
    assert not weather.calm


def test_gusts():
    weather = parse_metar_json(fixture("gusts"), "LSZH")
    assert (weather.wind_dir_deg, weather.wind_speed_kt, weather.gust_kt) == (240, 18, 32)
    assert (weather.temperature_c, weather.dewpoint_c, weather.qnh_hpa) == (12, 4, 1004)


def test_variable_wind_has_no_direction():
    weather = parse_metar_json(fixture("vrb"), "LSZH")
    assert weather.wind_variable
    assert weather.wind_dir_deg is None
    assert weather.wind_speed_kt == 2
    assert weather.temperature_c == -2


def test_calm_wind_has_no_direction():
    weather = parse_metar_json(fixture("calm"), "LSZH")
    assert weather.calm
    assert weather.wind_dir_deg is None
    assert not weather.wind_variable
    assert weather.observed_at == datetime(2026, 11, 27, 5, 50, tzinfo=UTC)


def test_missing_and_malformed_fields_become_none():
    weather = parse_metar_json(fixture("missing_fields"), "LSZH")
    assert weather is not None
    assert weather.wind_dir_deg is None
    assert weather.wind_speed_kt == 3
    assert weather.gust_kt is None
    assert (weather.temperature_c, weather.dewpoint_c, weather.qnh_hpa) == (None, None, None)
    # without obsTime the (rounded) reportTime is the fallback
    assert weather.observed_at == datetime(2026, 11, 26, 10, 20, tzinfo=UTC)


def test_newest_observation_of_the_station_wins():
    entries = fixture("recent_hours")
    entries.reverse()
    entries.insert(0, {**entries[0], "icaoId": "LSZB", "obsTime": 1_900_000_000})
    weather = parse_metar_json(entries, "lszh")
    assert weather.observed_at == NORMAL_OBSERVED


def test_float_values_are_rounded():
    entry = {**fixture("normal")[0], "temp": 1.7, "dewp": -0.4, "altim": 1013.2, "wspd": 5.0}
    weather = parse_metar_json([entry], "LSZH")
    assert (weather.temperature_c, weather.dewpoint_c, weather.qnh_hpa) == (2, 0, 1013)
    assert weather.wind_speed_kt == 5


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        None,
        "METAR LSZH",
        [None, 3, "x"],
        [{"icaoId": "LSZH", "wspd": 5}],  # no observation time
        [{"icaoId": "LSZH", "obsTime": 1795677600}],  # no wind speed
        [{"icaoId": "LSZH", "obsTime": 1795677600, "wspd": True}],
        [{"icaoId": "LSZH", "obsTime": "soon", "wspd": 5}],
        [{"obsTime": 1795677600, "wspd": 5}],  # no station
    ],
)
def test_unusable_payloads(payload):
    assert parse_metar_json(payload, "LSZH") is None


def test_other_station_only():
    assert parse_metar_json(fixture("normal"), "LSZB") is None


@pytest.mark.parametrize("wdir", [400, -10, "N", None, 12.5e300])
def test_bad_direction_is_dropped(wdir):
    entry = {**fixture("normal")[0], "wdir": wdir}
    weather = parse_metar_json([entry], "LSZH")
    assert weather.wind_dir_deg is None
    assert not weather.wind_variable


def test_gust_not_above_mean_is_ignored():
    entry = {**fixture("gusts")[0], "wgst": 18}
    assert parse_metar_json([entry], "LSZH").gust_kt is None


# --- fetching ---------------------------------------------------------------------------


def test_fetch_builds_request():
    opener = FakeOpener(FakeResponse(body("normal")))
    src = source(opener, timeout_s=4.0)
    weather = src.fetch()
    request, timeout = opener.requests[0]
    assert request.full_url == "https://aviationweather.gov/api/data/metar?ids=LSZH&format=json"
    assert request.get_header("User-agent") == USER_AGENT
    assert timeout == 4.0
    assert weather.wind_dir_deg == 10
    assert src.latest == weather


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (TimeoutError("timed out"), "timed out"),
        (urllib.error.URLError(TimeoutError("timed out")), "timed out"),
        (urllib.error.URLError("nodename nor servname provided"), "network error"),
        (ConnectionResetError("reset"), "reset"),
        (http_error(503), "HTTP 503"),
        (http_error(429), "HTTP 429"),
        (FakeResponse(b"{not json"), "malformed JSON"),
        (FakeResponse(b"\xff\xfe garbage"), "malformed JSON"),
        (FakeResponse(b""), "no usable observation"),
        (FakeResponse(b"[]"), "no usable observation"),
        (FakeResponse(b'{"error": "bad"}'), "no usable observation"),
    ],
)
def test_failures_return_none_and_log_one_warning(failure, reason, caplog):
    src = source(FakeOpener(failure))
    with caplog.at_level(logging.DEBUG, logger="zurich_air.data.metar"):
        assert src.fetch() is None
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    record = caplog.records[0]
    assert reason in record.getMessage()
    assert record.exc_info is None
    assert src.failures == 1
    assert src.latest is None
    assert reason in src.last_error


def test_last_error_clears_once_a_request_works():
    src = source(FakeOpener(http_error(500), FakeResponse(body("normal"))))
    assert src.last_error is None
    src.fetch()
    assert src.last_error == "HTTP 500"
    src.fetch()
    assert src.last_error is None


def test_weather_fetches_at_most_every_interval():
    clock = Clock()
    opener = FakeOpener(FakeResponse(body("normal")), FakeResponse(body("normal")))
    src = source(opener, clock)
    assert src.due()
    assert src.weather().wind_speed_kt == 5
    clock.t += 599
    assert not src.due()
    assert src.weather().wind_speed_kt == 5  # cached
    assert len(opener.requests) == 1
    clock.t += 1
    src.weather()
    assert len(opener.requests) == 2


def test_failure_keeps_previous_observation_and_waits_too():
    clock = Clock()
    opener = FakeOpener(FakeResponse(body("normal")), http_error(500))
    src = source(opener, clock, now=datetime(2026, 11, 26, 7, 40, tzinfo=UTC))
    first = src.weather()
    clock.t += 600
    assert src.weather() == first  # request failed, still fresh enough
    clock.t += 300
    assert src.weather() == first  # no retry before the interval
    assert len(opener.requests) == 2


def test_stale_observation_is_unavailable():
    opener = FakeOpener(FakeResponse(body("normal")))
    src = source(opener, now=NORMAL_OBSERVED + timedelta(seconds=5400))
    assert src.weather() is not None
    assert src.current(NORMAL_OBSERVED + timedelta(seconds=5401)) is None
    assert src.latest is not None  # kept, only hidden


def test_stale_on_arrival():
    src = source(FakeOpener(FakeResponse(body("normal"))), now=NORMAL_OBSERVED + timedelta(hours=3))
    assert src.weather() is None


def test_older_observation_does_not_replace_newer():
    opener = FakeOpener(FakeResponse(body("gusts")), FakeResponse(body("normal")))
    src = source(opener, now=datetime(2026, 11, 26, 16, 0, tzinfo=UTC))
    newer = src.fetch()
    src.fetch()
    assert src.latest == newer


def test_age():
    weather = parse_metar_json(fixture("normal"), "LSZH")
    assert weather.age_s(NORMAL_OBSERVED + timedelta(minutes=7)) == 420
