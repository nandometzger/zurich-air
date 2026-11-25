import email.message
import io
import json
import urllib.error

import pytest

from zurich_air.config import SOURCE_NAMES, ProviderConfig
from zurich_air.data.provider import (
    ADSB_LOL_KEY_ENV,
    PRESETS,
    USER_AGENT,
    FallbackProvider,
    ProviderError,
    ReadsbHttpSource,
    build_provider,
    make_source,
    radius_nm,
)

BODY = json.dumps(
    {
        "now": 1798123320000,
        "total": 1,
        "ac": [{"hex": "4b0001", "flight": "SWR001 ", "lat": 47.4, "lon": 8.5}],
    }
).encode()


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

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
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid", code, "error", _headers(headers or {}), None
    )


def source(opener, clock=None, **kwargs) -> ReadsbHttpSource:
    return ReadsbHttpSource(
        "test",
        "https://example.invalid/{lat}/{lon}/{radius_nm}",
        opener=opener,
        clock=clock or Clock(),
        **kwargs,
    )


def test_presets_match_config_names():
    assert set(PRESETS) == set(SOURCE_NAMES)


@pytest.mark.parametrize(("km", "nm"), [(90, 49), (1.852, 1), (0.1, 1), (10_000, 250)])
def test_radius_nm(km, nm):
    assert radius_nm(km) == nm


def test_success_builds_url_headers_and_snapshot():
    opener = FakeOpener(FakeResponse(BODY))
    src = source(opener, timeout_s=7.0)
    snapshot = src.fetch(47.3769, 8.5417, 90.0)
    request, timeout = opener.requests[0]
    assert request.full_url == "https://example.invalid/47.3769/8.5417/49"
    assert request.get_header("User-agent") == USER_AGENT
    assert (
        USER_AGENT.startswith("zurich-air/") and "@" not in USER_AGENT and "http" not in USER_AGENT
    )
    assert timeout == 7.0
    assert snapshot.source == "test"
    assert [a.callsign for a in snapshot.aircraft] == ["SWR001"]
    assert src.last_body == BODY


def test_preset_urls():
    fi = make_source("adsb_fi", env={}, opener=FakeOpener(FakeResponse(BODY)))
    fi.fetch(47.3769, 8.5417, 90.0)
    assert fi._opener.requests[0][0].full_url == (
        "https://opendata.adsb.fi/api/v3/lat/47.3769/lon/8.5417/dist/49"
    )
    lol = make_source("adsb_lol", env={}, opener=FakeOpener(FakeResponse(BODY)))
    lol.fetch(47.3769, 8.5417, 90.0)
    assert lol._opener.requests[0][0].full_url == "https://api.adsb.lol/v2/point/47.3769/8.5417/49"


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timed out"),
        urllib.error.URLError(TimeoutError("timed out")),
        urllib.error.URLError("nodename nor servname provided"),
        ConnectionResetError("reset"),
        http_error(500),
        FakeResponse(b"{not json"),
        FakeResponse(b"\xff\xfe garbage"),
        FakeResponse(b'{"msg": "error"}'),
    ],
)
def test_failures_raise_provider_error_and_back_off(failure):
    clock = Clock()
    src = source(FakeOpener(failure), clock)
    with pytest.raises(ProviderError, match=r"^test: "):
        src.fetch(47.4, 8.5, 90)
    assert src.wait_s() == 30.0
    clock.t += 30
    assert src.wait_s() == 0.0


def test_backoff_grows_and_resets_on_success():
    clock = Clock()
    src = source(
        FakeOpener(http_error(502), http_error(502), FakeResponse(BODY), http_error(502)), clock
    )
    with pytest.raises(ProviderError):
        src.fetch(47.4, 8.5, 90)
    clock.t += 30
    with pytest.raises(ProviderError):
        src.fetch(47.4, 8.5, 90)
    assert src.wait_s() == 60.0
    clock.t += 60
    src.fetch(47.4, 8.5, 90)
    with pytest.raises(ProviderError):
        src.fetch(47.4, 8.5, 90)
    assert src.wait_s() == 30.0


def test_429_honours_retry_after():
    clock = Clock()
    src = source(FakeOpener(http_error(429, {"Retry-After": "1200"})), clock)
    with pytest.raises(ProviderError, match="HTTP 429"):
        src.fetch(47.4, 8.5, 90)
    assert src.wait_s() == 1200.0
    clock.t += 1199
    assert src.wait_s() == pytest.approx(1.0)


def test_429_without_retry_after_cools_down_anyway():
    src = source(FakeOpener(http_error(429)))
    with pytest.raises(ProviderError):
        src.fetch(47.4, 8.5, 90)
    assert src.wait_s() >= 300


def test_min_interval():
    clock = Clock()
    src = source(FakeOpener(FakeResponse(BODY)), clock, min_interval_s=180)
    assert src.wait_s() == 0.0
    src.fetch(47.4, 8.5, 90)
    assert src.wait_s() == 180.0
    clock.t += 100
    assert src.wait_s() == 80.0


def test_adsb_lol_key_header_only_when_env_set():
    opener = FakeOpener(FakeResponse(BODY), FakeResponse(BODY), FakeResponse(BODY))
    make_source("adsb_lol", env={}, opener=opener).fetch(47.4, 8.5, 90)
    make_source("adsb_lol", env={ADSB_LOL_KEY_ENV: "  "}, opener=opener).fetch(47.4, 8.5, 90)
    keyed = make_source("adsb_lol", env={ADSB_LOL_KEY_ENV: "secret-key"}, opener=opener)
    keyed.fetch(47.4, 8.5, 90)
    headers = [req.get_header("X-api-key") for req, _ in opener.requests]
    assert headers == [None, None, "secret-key"]
    assert "secret-key" not in repr(keyed)


def test_adsb_fi_never_sends_a_key():
    opener = FakeOpener(FakeResponse(BODY))
    make_source("adsb_fi", env={ADSB_LOL_KEY_ENV: "secret-key"}, opener=opener).fetch(47.4, 8.5, 90)
    request = opener.requests[0][0]
    assert all("secret-key" not in v for v in request.headers.values())


def test_unknown_preset():
    with pytest.raises(ValueError, match="unknown source"):
        make_source("flightradar")


def test_build_provider_follows_config_order():
    provider = build_provider(ProviderConfig(sources=("adsb_lol", "adsb_fi"), timeout_s=3), env={})
    assert [s.label for s in provider.sources] == ["adsb.lol", "adsb.fi"]
    assert all(s.timeout_s == 3 for s in provider.sources)


# --- fallback chain ---------------------------------------------------------------------


def chain(clock, primary_outcomes, fallback_outcomes, fallback_interval=180):
    primary = source(FakeOpener(*primary_outcomes), clock)
    primary.label = "primary"
    fallback = source(FakeOpener(*fallback_outcomes), clock, min_interval_s=fallback_interval)
    fallback.label = "fallback"
    return FallbackProvider([primary, fallback]), primary, fallback


def test_primary_used_when_healthy():
    clock = Clock()
    provider, primary, fallback = chain(clock, [FakeResponse(BODY)], [])
    assert provider.fetch(47.4, 8.5, 90).source == "primary"
    assert provider.last_source is primary
    assert fallback._opener.requests == []


def test_falls_back_in_order_when_primary_fails():
    clock = Clock()
    provider, primary, fallback = chain(clock, [http_error(500)], [FakeResponse(BODY)])
    assert provider.fetch(47.4, 8.5, 90).source == "fallback"
    assert provider.last_source is fallback
    assert len(primary._opener.requests) == 1


def test_skips_sources_that_are_cooling_down_or_too_soon():
    clock = Clock()
    provider, primary, fallback = chain(
        clock, [http_error(500), FakeResponse(BODY)], [FakeResponse(BODY)]
    )
    assert provider.fetch(47.4, 8.5, 90).source == "fallback"
    clock.t += 10  # primary still backing off, fallback within its min interval
    with pytest.raises(ProviderError, match="primary ready in 20 s; fallback ready in 170 s"):
        provider.fetch(47.4, 8.5, 90)
    assert len(primary._opener.requests) == 1
    assert len(fallback._opener.requests) == 1
    clock.t += 20  # primary has recovered
    assert provider.fetch(47.4, 8.5, 90).source == "primary"


def test_all_sources_failing_raises():
    clock = Clock()
    provider, _, _ = chain(clock, [TimeoutError()], [http_error(429, {"Retry-After": "60"})])
    with pytest.raises(ProviderError, match="primary: timed out; fallback: HTTP 429"):
        provider.fetch(47.4, 8.5, 90)


def test_empty_chain_rejected():
    with pytest.raises(ValueError):
        FallbackProvider([])


def test_failed_requests_are_counted_per_source():
    clock = Clock()
    provider, _, _ = chain(
        clock,
        [http_error(500), TimeoutError()],
        [FakeResponse(BODY), http_error(429, {"Retry-After": "60"})],
    )
    assert provider.failure_counts() == {"primary": 0, "fallback": 0}
    provider.fetch(47.4, 8.5, 90)
    with pytest.raises(ProviderError):
        provider.fetch(47.4, 8.5, 90)  # both skipped: not counted as failures
    clock.t += 200  # both may be asked again
    with pytest.raises(ProviderError):
        provider.fetch(47.4, 8.5, 90)
    assert provider.failure_counts() == {"primary": 2, "fallback": 1}
