import email.message
import gzip
import io
import json
import urllib.error

import pytest

from zurich_air.data.provider import USER_AGENT
from zurich_air.data.routes import (
    INDEX_NAME,
    MAX_STALE_S,
    TTL_S,
    VRS_ROUTES_URL,
    Route,
    RouteTable,
    normalise_callsign,
    parse_routes,
)

SWR_CSV = (
    "\ufeffCallsign,Code,Number,AirlineCode,AirportCodes\n"
    "SWR8,SWR,8,SWR,LSZH-KJFK\n"
    "SWR9,SWR,9,SWR,KJFK-LSZH\n"
    "SWR16,SWR,16,SWR,LSZH-VTBS-WSSS\n"
    "SWR318,SWR,318,SWR,EGLL-LSZH\n"
).encode()
RYR7_CSV = b"Callsign,Code,Number,AirlineCode,AirportCodes\nRYR7YR,RYR,7YR,RYR,LEMG-EDDB\n"
RYR1_CSV = b"Callsign,Code,Number,AirlineCode,AirportCodes\nRYR123,RYR,123,RYR,EGSS-LIME\n"


class Clock:
    def __init__(self) -> None:
        self.t = 1_795_680_000.0  # wall-clock seconds, late November 2026

    def __call__(self) -> float:
        return self.t


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = _headers(headers or {})
        self.status = 200


def _headers(values: dict[str, str]) -> email.message.Message:
    msg = email.message.Message()
    for k, v in values.items():
        msg[k] = v
    return msg


class FakeOpener:
    """Stands in for urllib.request.urlopen: returns or raises the queued outcomes."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if not self.outcomes:
            raise AssertionError(f"unexpected request for {request.full_url}")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    @property
    def urls(self) -> list[str]:
        return [request.full_url for request, _ in self.requests]


def http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid", code, "error", _headers(headers or {}), None
    )


def url(name: str) -> str:
    return f"{VRS_ROUTES_URL}/{name[0]}/{name}.csv"


@pytest.fixture
def clock():
    return Clock()


def table(tmp_path, opener, clock, **kwargs) -> RouteTable:
    return RouteTable(tmp_path / "routes", opener=opener, clock=clock, **kwargs)


# -- callsigns and parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("callsign", "normalised"),
    [
        ("SWR8", "SWR8"),
        ("swr8  ", "SWR8"),
        ("EZY39NR", "EZY39NR"),
        ("UAE87", "UAE87"),
        ("EJU72XB", "EJU72XB"),
        ("EZY0039", "EZY39"),
        ("EZY00AB", "EZY0AB"),
        ("EZY0000", "EZY0"),
    ],
)
def test_normalise_airline_callsigns(callsign, normalised):
    assert normalise_callsign(callsign) == normalised


@pytest.mark.parametrize(
    "callsign",
    [None, "", "   ", "HBJVK", "DEABC", "N123AB", "FLORIA11", "SW123", "SWR12345", "SWR1ABC"],
)
def test_non_airline_callsigns_are_rejected(callsign):
    assert normalise_callsign(callsign) is None


def test_parse_vrs_csv_including_multi_leg_routes():
    routes = list(parse_routes(SWR_CSV.decode()))
    assert routes[0] == Route("SWR8", ("LSZH", "KJFK"))
    assert routes[2] == Route("SWR16", ("LSZH", "VTBS", "WSSS"))
    assert routes[2].legs == (("LSZH", "VTBS"), ("VTBS", "WSSS"))
    assert len(routes) == 4


def test_parse_finds_columns_by_heading_and_skips_bad_rows():
    text = (
        'AirportCodes,Extra,Callsign\n"EGLL-LSZH","a, b",BAW708\n'
        "LSZH,x,SWR1\n"  # a single airport is no route
        "LSZH-??,x,SWR2\n"  # not an airport code
        "LSZH-EDDF,x,HBJVK\n"  # not an airline callsign
        "LSZH-EDDF\n"  # too few columns
        "LSZH-LIMC,x,SWR0012\n"  # normalised on the way in
    )
    assert list(parse_routes(text)) == [
        Route("BAW708", ("EGLL", "LSZH")),
        Route("SWR12", ("LSZH", "LIMC")),
    ]


@pytest.mark.parametrize("text", ["", "<html>Sign in to the network</html>\n"])
def test_parse_rejects_files_without_the_columns(text):
    with pytest.raises(ValueError, match="not a VRS routes file"):
        list(parse_routes(text))


# -- fetching and caching -----------------------------------------------------------------


def test_lookup_fetches_the_airline_file_once(tmp_path, clock):
    opener = FakeOpener(FakeResponse(SWR_CSV, {"ETag": '"v1"'}))
    routes = table(tmp_path, opener, clock, timeout_s=4.0)
    assert routes.lookup("SWR8") == Route("SWR8", ("LSZH", "KJFK"))
    assert routes.lookup("SWR16") == Route("SWR16", ("LSZH", "VTBS", "WSSS"))
    assert routes.lookup("SWR999") is None  # not in the table: no further request
    request, timeout = opener.requests[0]
    assert opener.urls == [url("SWR-all")]
    assert request.get_header("User-agent") == USER_AGENT
    assert request.get_header("Accept-encoding") == "gzip"
    assert timeout == 4.0
    assert (routes.requests, routes.failures, routes.bytes_received) == (1, 0, len(SWR_CSV))


def test_gzip_responses_are_decompressed(tmp_path, clock):
    opener = FakeOpener(FakeResponse(gzip.compress(SWR_CSV), {"Content-Encoding": "gzip"}))
    assert table(tmp_path, opener, clock).lookup("SWR9") == Route("SWR9", ("KJFK", "LSZH"))


def test_cache_survives_a_restart_and_stays_small(tmp_path, clock):
    table(tmp_path, FakeOpener(FakeResponse(SWR_CSV)), clock).lookup("SWR8")
    restarted = table(tmp_path, FakeOpener(), clock)  # any request would fail the test
    assert restarted.lookup("SWR318") == Route("SWR318", ("EGLL", "LSZH"))
    cached = (tmp_path / "routes" / "SWR-all.csv").read_text()
    assert cached.splitlines()[:2] == ["callsign,airports", "SWR8,LSZH-KJFK"]
    assert sorted(p.name for p in (tmp_path / "routes").iterdir()) == ["SWR-all.csv", INDEX_NAME]


def test_lookups_without_an_airline_callsign_never_fetch(tmp_path, clock):
    routes = table(tmp_path, FakeOpener(), clock)
    for callsign in (None, "", "HBJVK", "N123AB", "FLORIA11"):
        assert routes.lookup(callsign) is None
    assert routes.requests == 0


def test_revalidates_with_etag_after_the_ttl(tmp_path, clock):
    opener = FakeOpener(FakeResponse(SWR_CSV, {"ETag": '"v1"'}), http_error(304))
    routes = table(tmp_path, opener, clock)
    routes.lookup("SWR8")
    clock.t += TTL_S - 1
    assert routes.lookup("SWR8") is not None
    assert len(opener.requests) == 1  # still fresh
    clock.t += 2
    assert routes.lookup("SWR8") == Route("SWR8", ("LSZH", "KJFK"))
    assert opener.requests[1][0].get_header("If-none-match") == '"v1"'
    clock.t += TTL_S - 1
    routes.lookup("SWR8")
    assert len(opener.requests) == 2  # the 304 renewed the TTL


def test_refresh_replaces_changed_routes(tmp_path, clock):
    changed = SWR_CSV.replace(b"LSZH-KJFK", b"LSZH-KEWR")
    opener = FakeOpener(FakeResponse(SWR_CSV), FakeResponse(changed))
    routes = table(tmp_path, opener, clock)
    routes.lookup("SWR8")
    clock.t += TTL_S
    assert routes.lookup("SWR8") == Route("SWR8", ("LSZH", "KEWR"))
    assert opener.requests[1][0].get_header("If-none-match") is None  # no ETag was given


def test_stale_data_is_used_while_refresh_fails_up_to_a_limit(tmp_path, clock):
    down = urllib.error.URLError(OSError("network is unreachable"))
    opener = FakeOpener(FakeResponse(SWR_CSV), down, down)
    routes = table(tmp_path, opener, clock)
    routes.lookup("SWR8")
    clock.t += TTL_S + 1
    assert routes.lookup("SWR8") == Route("SWR8", ("LSZH", "KJFK"))  # refresh failed
    clock.t += MAX_STALE_S
    assert routes.lookup("SWR8") is None  # too old to trust
    assert routes.failures == 2


def test_airlines_split_by_first_digit(tmp_path, clock):
    opener = FakeOpener(http_error(404), FakeResponse(RYR7_CSV), FakeResponse(RYR1_CSV))
    routes = table(tmp_path, opener, clock, fetch_budget=10)
    assert routes.lookup("RYR7YR") == Route("RYR7YR", ("LEMG", "EDDB"))
    assert routes.lookup("RYR0123") == Route("RYR123", ("EGSS", "LIME"))
    assert routes.lookup("RYR7AB") is None  # RYR-7 is cached: no request
    assert opener.urls == [url("RYR-all"), url("RYR-7"), url("RYR-1")]


def test_unknown_airline_is_remembered_until_the_ttl(tmp_path, clock):
    opener = FakeOpener(http_error(404), http_error(404), http_error(404), http_error(404))
    routes = table(tmp_path, opener, clock)
    assert routes.lookup("QQQ12") is None
    assert routes.lookup("QQQ12") is None
    assert len(opener.requests) == 2  # QQQ-all and QQQ-1, once each
    assert routes.failures == 0  # a 404 is an answer, not a failure
    clock.t += TTL_S
    assert routes.lookup("QQQ12") is None
    assert len(opener.requests) == 4


def test_fetch_budget_per_period(tmp_path, clock):
    body = b"Callsign,AirportCodes\n"
    opener = FakeOpener(*(FakeResponse(body) for _ in range(4)))
    routes = table(tmp_path, opener, clock, fetch_budget=2, budget_period_s=30.0)
    for callsign in ("AAA1", "BBB1", "CCC1"):
        routes.lookup(callsign)
    assert opener.urls == [url("AAA-all"), url("BBB-all")]  # CCC waits for the next update
    clock.t += 29
    routes.lookup("CCC1")
    assert len(opener.requests) == 2
    clock.t += 1
    routes.lookup("CCC1")
    routes.lookup("DDD1")
    assert opener.urls[2:] == [url("CCC-all"), url("DDD-all")]


def test_cold_cache_fills_over_several_updates(tmp_path, clock):
    opener = FakeOpener(http_error(404), FakeResponse(RYR7_CSV))
    routes = table(tmp_path, opener, clock, fetch_budget=1, budget_period_s=30.0)
    assert routes.lookup("RYR7YR") is None  # RYR-all checked; RYR-7 has to wait
    clock.t += 60
    assert routes.lookup("RYR7YR") == Route("RYR7YR", ("LEMG", "EDDB"))


def test_zero_budget_uses_only_the_cache(tmp_path, clock):
    table(tmp_path, FakeOpener(FakeResponse(SWR_CSV)), clock).lookup("SWR8")
    offline = table(tmp_path, FakeOpener(), clock, fetch_budget=0)
    assert offline.lookup("SWR9") == Route("SWR9", ("KJFK", "LSZH"))
    assert offline.lookup("DLH4") is None


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError(OSError("name resolution failed")),
        TimeoutError("timed out"),
        http_error(500),
        FakeResponse(b"<html>captive portal</html>"),
        FakeResponse(b"not gzip", {"Content-Encoding": "gzip"}),
        FakeResponse(gzip.compress(SWR_CSV)[:-20], {"Content-Encoding": "gzip"}),
    ],
)
def test_failures_back_off_and_are_not_cached(tmp_path, clock, failure):
    opener = FakeOpener(failure, FakeResponse(SWR_CSV))
    routes = table(tmp_path, opener, clock)
    assert routes.lookup("SWR8") is None
    clock.t += 29
    assert routes.lookup("SWR8") is None
    assert len(opener.requests) == 1  # resting for 30 s
    clock.t += 1
    assert routes.lookup("SWR8") == Route("SWR8", ("LSZH", "KJFK"))
    assert routes.failures == 1


def test_backoff_grows_with_repeated_failures(tmp_path, clock):
    down = urllib.error.URLError(OSError("network is unreachable"))
    opener = FakeOpener(down, down, down, FakeResponse(SWR_CSV))
    routes = table(tmp_path, opener, clock)
    for wait in (30, 60, 120):
        routes.lookup("SWR8")
        clock.t += wait - 1
        routes.lookup("SWR8")
        clock.t += 1
    assert len(opener.requests) == 3
    assert routes.lookup("SWR8") is not None


@pytest.mark.parametrize(("headers", "cooldown"), [({"Retry-After": "120"}, 120), ({}, 600)])
def test_rate_limit_honours_retry_after(tmp_path, clock, headers, cooldown):
    opener = FakeOpener(http_error(429, headers), FakeResponse(SWR_CSV))
    routes = table(tmp_path, opener, clock)
    assert routes.lookup("SWR8") is None
    clock.t += cooldown - 1
    assert routes.lookup("SWR8") is None
    assert len(opener.requests) == 1
    clock.t += 1
    assert routes.lookup("SWR8") is not None


def test_oversized_response_is_refused(tmp_path, clock, monkeypatch):
    monkeypatch.setattr("zurich_air.data.routes.MAX_FILE_BYTES", 64)
    routes = table(tmp_path, FakeOpener(FakeResponse(SWR_CSV)), clock)
    assert routes.lookup("SWR8") is None
    assert routes.failures == 1


def test_corrupt_index_starts_afresh(tmp_path, clock):
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / INDEX_NAME).write_text("{not json")
    opener = FakeOpener(FakeResponse(SWR_CSV))
    assert table(tmp_path, opener, clock).lookup("SWR8") is not None
    index = json.loads((tmp_path / "routes" / INDEX_NAME).read_text())
    assert index["files"]["SWR-all"]["found"] is True


def test_vanished_data_file_is_fetched_again_without_etag(tmp_path, clock):
    opener = FakeOpener(FakeResponse(SWR_CSV, {"ETag": '"v1"'}), FakeResponse(SWR_CSV))
    routes = table(tmp_path, opener, clock)
    routes.lookup("SWR8")
    (tmp_path / "routes" / "SWR-all.csv").unlink()
    assert routes.lookup("SWR8") is None
    assert routes.lookup("SWR8") is not None
    assert opener.requests[1][0].get_header("If-none-match") is None


def test_unwritable_cache_is_a_failure(tmp_path, clock):
    (tmp_path / "routes").write_text("a file where the cache directory should be")
    routes = table(tmp_path, FakeOpener(FakeResponse(SWR_CSV)), clock)
    assert routes.lookup("SWR8") is None
    assert routes.failures == 1


def test_invalid_limits_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="invalid"):
        RouteTable(tmp_path, ttl_s=10, max_stale_s=5)
