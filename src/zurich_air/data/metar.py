"""Current weather at the airport from its METAR, via aviationweather.gov.

The Aviation Weather Center's data API needs no account::

    GET https://aviationweather.gov/api/data/metar?ids=LSZH&format=json
    [{"icaoId": "LSZH", "obsTime": 1795677600, "wdir": 10, "wspd": 5, "temp": 0,
      "dewp": -3, "altim": 1012, "rawOb": "METAR LSZH 260720Z 01005KT ...", ...}]

``wdir`` is degrees true or ``"VRB"``, ``wspd`` and ``wgst`` (absent without gusts) are
knots, ``temp`` and ``dewp`` degrees Celsius, ``altim`` the QNH in hPa and ``obsTime`` the
observation time in epoch seconds (``reportTime`` is rounded, so it is only a fallback).
Like the traffic parser, :func:`parse_metar_json` coerces every field defensively: a bad
value becomes ``None`` and never raises.

Zürich issues a METAR at :20 and :50, available two to six minutes later, so
:class:`MetarSource` asks at most every ``interval_s`` (10 minutes) and treats an
observation older than ``max_age_s`` (90 minutes) as unavailable. The service allows 100
requests a minute. Its data comes from the US National Weather Service and is in the
public domain; using it implies no endorsement by the NWS. Only the standard library is
used.
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from zurich_air.data.provider import USER_AGENT, Clock, Opener, failure_reason

log = logging.getLogger(__name__)

METAR_URL = "https://aviationweather.gov/api/data/metar?ids={station}&format=json"
MAX_RESPONSE_BYTES = 1024 * 1024
VARIABLE_WIND = "VRB"


@dataclass(frozen=True)
class Weather:
    """One METAR observation, reduced to what the display can show."""

    station: str  # ICAO location indicator, e.g. "LSZH"
    observed_at: datetime  # timezone-aware UTC
    wind_dir_deg: int | None  # degrees true; None when variable or calm
    wind_variable: bool  # reported as VRB
    wind_speed_kt: int
    gust_kt: int | None
    temperature_c: int | None
    dewpoint_c: int | None
    qnh_hpa: int | None
    raw: str  # the METAR as issued, "" if missing

    @property
    def calm(self) -> bool:
        """Wind reported as 00000KT."""
        return self.wind_speed_kt == 0

    def age_s(self, now: datetime) -> float:
        """Seconds between the observation and *now* (timezone-aware)."""
        return (now - self.observed_at).total_seconds()


def parse_metar_json(payload: Any, station: str) -> Weather | None:
    """The newest observation for *station* in a decoded API *payload*.

    Returns ``None`` when the payload holds no usable observation for the station: not a
    list, empty, or without an observation time or wind speed.
    """
    if not isinstance(payload, list):
        return None
    wanted = station.strip().upper()
    observations = [w for w in map(_parse_entry, payload) if w is not None]
    matching = [w for w in observations if w.station == wanted]
    return max(matching, key=lambda w: w.observed_at, default=None)


def _parse_entry(entry: Any) -> Weather | None:
    if not isinstance(entry, Mapping):
        return None
    station = _str(entry.get("icaoId"))
    observed_at = _epoch(entry.get("obsTime")) or _iso(entry.get("reportTime"))
    speed = _int(entry.get("wspd"))
    if station is None or observed_at is None or speed is None or speed < 0:
        return None
    direction_field = entry.get("wdir")
    variable = isinstance(direction_field, str) and direction_field.strip() == VARIABLE_WIND
    direction = None if variable or speed == 0 else _int(direction_field)
    if direction is not None and not 0 <= direction <= 360:
        direction = None
    gust = _int(entry.get("wgst"))
    return Weather(
        station=station.upper(),
        observed_at=observed_at,
        wind_dir_deg=direction,
        wind_variable=variable,
        wind_speed_kt=speed,
        gust_kt=gust if gust is not None and gust > speed else None,
        temperature_c=_int(entry.get("temp")),
        dewpoint_c=_int(entry.get("dewp")),
        qnh_hpa=_int(entry.get("altim")),
        raw=_str(entry.get("rawOb")) or "",
    )


class MetarSource:
    """Fetches the METAR of one station, politely and without ever raising.

    :meth:`fetch` makes one request and returns ``None`` on any failure, logging a single
    warning line. :meth:`weather` is the policy the app uses: it fetches at most every
    ``interval_s`` (failed attempts count too), keeps the newest observation and hides it
    once it is older than ``max_age_s``.
    """

    def __init__(
        self,
        station: str = "LSZH",
        *,
        interval_s: float = 600.0,
        max_age_s: float = 5400.0,
        timeout_s: float = 10.0,
        url_template: str = METAR_URL,
        opener: Opener = urllib.request.urlopen,
        clock: Clock = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.station = station.strip().upper()
        self.interval_s = interval_s
        self.max_age_s = max_age_s
        self.timeout_s = timeout_s
        self.url = url_template.format(station=quote(self.station))
        self._opener = opener
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_attempt: float | None = None
        self.latest: Weather | None = None  # newest observation received, however old
        self.failures = 0  # failed requests since start

    def __repr__(self) -> str:
        return f"MetarSource({self.station!r})"

    def due(self) -> bool:
        """True when ``interval_s`` has passed since the last request (or there was none)."""
        if self._last_attempt is None:
            return True
        return self._clock() - self._last_attempt >= self.interval_s

    def fetch(self) -> Weather | None:
        """Request the current observation now; ``None`` on any failure."""
        self._last_attempt = self._clock()
        request = urllib.request.Request(
            self.url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            weather = parse_metar_json(json.loads(body) if body.strip() else [], self.station)
        except urllib.error.HTTPError as exc:
            return self._failed(f"HTTP {exc.code}")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            return self._failed(failure_reason(exc))
        if weather is None:
            return self._failed("no usable observation")
        if self.latest is None or weather.observed_at >= self.latest.observed_at:
            self.latest = weather
        log.debug("metar %s: %s", self.station, weather.raw)
        return weather

    def weather(self, now: datetime | None = None) -> Weather | None:
        """The newest observation, refreshed when due; ``None`` once older than ``max_age_s``."""
        if self.due():
            self.fetch()
        return self.current(now)

    def current(self, now: datetime | None = None) -> Weather | None:
        """The newest observation already received, if not older than ``max_age_s``."""
        if self.latest is None:
            return None
        now = self._wall_clock() if now is None else now
        return self.latest if self.latest.age_s(now) <= self.max_age_s else None

    def _failed(self, reason: str) -> None:
        self.failures += 1
        log.warning("metar %s: %s; next try in %.0f s", self.station, reason, self.interval_s)
        return None


def _str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _int(value: Any) -> int | None:
    """A whole number from an int, a finite float or a numeric string; else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return round(value)
    return None


def _epoch(value: Any) -> datetime | None:
    seconds = _int(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _iso(value: Any) -> datetime | None:
    text = _str(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
