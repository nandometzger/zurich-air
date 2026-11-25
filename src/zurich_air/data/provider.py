"""Fetch live traffic from community ADS-B aggregators over HTTP.

Both supported services speak the same readsb JSON API and need no account:

* ``adsb_fi`` — opendata.adsb.fi, the default. About 1 request/s; personal,
  non-commercial use; attribution required.
* ``adsb_lol`` — api.adsb.lol, the fallback. Without an API key the quota is 500
  requests/day per IP, so it is polled at most every 180 s (480/day). An optional key
  is read from the environment variable ``ZURICH_AIR_ADSB_LOL_KEY``, never from the
  config file.

:class:`FallbackProvider` tries the configured sources in order. A source that just
failed backs off (honouring ``Retry-After`` after HTTP 429) and is skipped until it
has recovered, as is one whose minimum request interval has not yet elapsed. Only the
standard library is used.
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from zurich_air import __version__
from zurich_air.config import ProviderConfig
from zurich_air.data.aircraft import TrafficSnapshot
from zurich_air.data.readsb import parse_snapshot
from zurich_air.processing.geo import KM_PER_NM

log = logging.getLogger(__name__)

USER_AGENT = f"zurich-air/{__version__}"  # deliberately generic: no URL, name or e-mail
MAX_RADIUS_NM = 250
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
ERROR_BACKOFF_S = (30.0, 60.0, 120.0, 300.0)  # after 1, 2, 3, 4+ consecutive errors
RATE_LIMIT_COOLDOWN_S = 600.0  # after HTTP 429 without a usable Retry-After
MAX_COOLDOWN_S = 24 * 3600.0
ADSB_LOL_KEY_ENV = "ZURICH_AIR_ADSB_LOL_KEY"


class ProviderError(Exception):
    """No traffic data could be fetched."""


class TrafficProvider(Protocol):
    def fetch(self, lat: float, lon: float, radius_km: float) -> TrafficSnapshot:
        """Aircraft within *radius_km* of (lat, lon); raises :class:`ProviderError`."""
        ...


@dataclass(frozen=True)
class SourcePreset:
    label: str  # shown in logs and, uppercased, as attribution on the display
    url_template: str  # with {lat}, {lon} and {radius_nm} placeholders
    min_interval_s: float
    key_env: str | None = None  # environment variable holding an optional API key
    key_header: str | None = None


PRESETS: dict[str, SourcePreset] = {
    "adsb_fi": SourcePreset(
        label="adsb.fi",
        url_template="https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{radius_nm}",
        min_interval_s=5.0,
    ),
    "adsb_lol": SourcePreset(
        label="adsb.lol",
        url_template="https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}",
        min_interval_s=180.0,
        key_env=ADSB_LOL_KEY_ENV,
        key_header="x-api-key",
    ),
}

Opener = Callable[..., Any]  # urllib.request.urlopen(request, timeout=...)
Clock = Callable[[], float]  # monotonic seconds


def radius_nm(radius_km: float) -> int:
    """Whole nautical miles covering *radius_km*, within the APIs' limit of 1 to 250 nm."""
    return max(1, min(MAX_RADIUS_NM, math.ceil(radius_km / KM_PER_NM)))


class ReadsbHttpSource:
    """One readsb-compatible HTTP endpoint with its own rate limit and backoff state."""

    def __init__(
        self,
        label: str,
        url_template: str,
        *,
        min_interval_s: float = 0.0,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = 10.0,
        opener: Opener = urllib.request.urlopen,
        clock: Clock = time.monotonic,
    ) -> None:
        self.label = label
        self.url_template = url_template
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        self._opener = opener
        self._clock = clock
        self._last_attempt: float | None = None
        self._cooldown_until = 0.0
        self._errors = 0
        self.failures = 0  # failed requests since start, for the hourly log summary
        self.last_body: bytes | None = None  # raw body of the last successful response

    def __repr__(self) -> str:  # never include headers: they may hold an API key
        return f"ReadsbHttpSource({self.label!r})"

    def wait_s(self) -> float:
        """Seconds until this source may be asked again (0 when ready)."""
        now = self._clock()
        ready_at = self._cooldown_until
        if self._last_attempt is not None:
            ready_at = max(ready_at, self._last_attempt + self.min_interval_s)
        return max(0.0, ready_at - now)

    def fetch(self, lat: float, lon: float, radius_km: float) -> TrafficSnapshot:
        url = self.url_template.format(
            lat=f"{lat:.4f}", lon=f"{lon:.4f}", radius_nm=radius_nm(radius_km)
        )
        request = urllib.request.Request(url, headers=self._headers)
        start = self._clock()
        self._last_attempt = start
        quota = None
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                quota = _quota(response.headers)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            snapshot = parse_snapshot(
                json.loads(body), source=self.label, fetched_at=datetime.now(UTC)
            )
        except urllib.error.HTTPError as exc:
            retry_after = _retry_after_s(exc.headers.get("Retry-After") if exc.headers else None)
            if exc.code == 429:
                cooldown = RATE_LIMIT_COOLDOWN_S if retry_after is None else retry_after
                self._cool_down(cooldown, "HTTP 429 rate limited")
            else:
                self._back_off(f"HTTP {exc.code}", retry_after)
            raise ProviderError(f"{self.label}: HTTP {exc.code}") from exc
        except (OSError, http.client.HTTPException, ValueError) as exc:
            reason = _describe(exc)
            self._back_off(reason)
            raise ProviderError(f"{self.label}: {reason}") from exc

        self._errors = 0
        self._cooldown_until = 0.0
        self.last_body = body
        elapsed = self._clock() - start
        extra = f", quota {quota} left" if quota is not None else ""
        log.debug(  # the app logs one line per update with the essentials
            "%s: %d aircraft (%d reported) in %.1f s%s",
            self.label,
            len(snapshot.aircraft),
            snapshot.total_reported,
            elapsed,
            extra,
        )
        return snapshot

    def _back_off(self, reason: str, retry_after: float | None = None) -> None:
        self._errors += 1
        delay = ERROR_BACKOFF_S[min(self._errors, len(ERROR_BACKOFF_S)) - 1]
        self._cool_down(max(delay, retry_after or 0.0), reason)

    def _cool_down(self, seconds: float, reason: str) -> None:
        """Called once for every failed request."""
        self.failures += 1
        seconds = min(seconds, MAX_COOLDOWN_S)
        self._cooldown_until = self._clock() + seconds
        log.warning("%s: %s; next try in %.0f s", self.label, reason, seconds)


class FallbackProvider:
    """Tries each source in order and returns the first snapshot that arrives."""

    def __init__(self, sources: Sequence[ReadsbHttpSource]) -> None:
        if not sources:
            raise ValueError("at least one source is required")
        self.sources = tuple(sources)
        self.last_source: ReadsbHttpSource | None = None

    def failure_counts(self) -> dict[str, int]:
        """Failed requests per source label since start (skipped sources not counted)."""
        return {source.label: source.failures for source in self.sources}

    def fetch(self, lat: float, lon: float, radius_km: float) -> TrafficSnapshot:
        problems = []
        for source in self.sources:
            wait = source.wait_s()
            if wait > 0:
                log.debug("%s: skipped, ready in %.0f s", source.label, wait)
                problems.append(f"{source.label} ready in {wait:.0f} s")
                continue
            try:
                snapshot = source.fetch(lat, lon, radius_km)
            except ProviderError as exc:
                problems.append(str(exc))
                continue
            self.last_source = source
            return snapshot
        raise ProviderError("no data: " + "; ".join(problems))


def make_source(
    name: str,
    *,
    timeout_s: float = 10.0,
    env: Mapping[str, str] | None = None,
    opener: Opener = urllib.request.urlopen,
    clock: Clock = time.monotonic,
) -> ReadsbHttpSource:
    """A source from the preset *name*, with its API key if one is set in *env*."""
    try:
        preset = PRESETS[name]
    except KeyError:
        raise ValueError(f"unknown source {name!r}; choose from {', '.join(PRESETS)}") from None
    env = os.environ if env is None else env
    headers = {}
    if preset.key_env and preset.key_header:
        key = env.get(preset.key_env, "").strip()
        if key:
            headers[preset.key_header] = key
    return ReadsbHttpSource(
        preset.label,
        preset.url_template,
        min_interval_s=preset.min_interval_s,
        headers=headers,
        timeout_s=timeout_s,
        opener=opener,
        clock=clock,
    )


def build_provider(
    config: ProviderConfig, env: Mapping[str, str] | None = None
) -> FallbackProvider:
    """The fallback chain described by the ``[provider]`` config section."""
    return FallbackProvider(
        [make_source(name, timeout_s=config.timeout_s, env=env) for name in config.sources]
    )


def _retry_after_s(value: str | None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _quota(headers: Any) -> str | None:
    if headers is None:
        return None
    remaining = headers.get("x-ratelimit-remaining")
    limit = headers.get("x-ratelimit-limit")
    if remaining is None:
        return None
    return f"{remaining}/{limit}" if limit else str(remaining)


def _describe(exc: BaseException) -> str:
    """A short, log-friendly reason for a failed request."""
    if isinstance(exc, urllib.error.URLError):
        if not isinstance(exc.reason, BaseException):
            return _shorten(f"network error: {exc.reason}")
        exc = exc.reason
    if isinstance(exc, TimeoutError):
        return "timed out"
    if isinstance(exc, json.JSONDecodeError | UnicodeDecodeError):
        return "malformed JSON"
    return _shorten(str(exc) or type(exc).__name__)


def _shorten(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
