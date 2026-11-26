"""Scheduled routes of airline flights, from an offline table built on demand.

ADS-B carries no flight plan. Routes come from the standing data of Virtual Radar Server
(https://github.com/vradarserver/standing-data, CC0 1.0): a crowd-sourced table that maps a
normalised callsign such as ``SWR8`` to the ICAO codes of the airports it serves, in the
order flown (``LSZH-KJFK``; multi-leg routes as ``A-B-C``), updated daily. A route from
this table is a schedule, not an observation: ``processing/route_check.py`` decides whether
the aircraft is actually flying it before anything is shown.

The repository keeps one CSV file per airline code, ``routes/schema-01/S/SWR-all.csv``;
an airline with more than 10,000 routes is split by the first digit of the flight number
instead (``R/RYR-7.csv``). Downloading all 1,600 files (19 MB) or a third-party bulk
mirror every week would be wasteful on a Pi on someone's home Wi-Fi, and holding all
630,000 routes in memory impossible. So :class:`RouteTable` fetches only the files of the
airlines actually seen, on demand:

* **Small requests.** One file is 1 kB to 250 kB, gzip-compressed on the wire to about a
  quarter. Only the airline code (and, for split airlines, one digit) leaves the device.
* **Disk cache with a TTL.** Each file is normalised to ``callsign,airports`` lines and
  kept in *cache_dir* for ``ttl_s`` (7 days), then revalidated with its ETag (a 304 costs
  no body). Stale data stays in use while it cannot be refreshed, for up to
  ``max_stale_s`` (30 days). A 404 is cached the same way, so unknown codes are not
  asked for again within the TTL.
* **Little memory.** Nothing is held in memory but a small index; a lookup reads one
  cached file and searches it for the callsign.
* **Never blocks for long.** At most ``fetch_budget`` requests are made per
  ``budget_period_s``, each with a timeout. A lookup whose file is not cached yet returns
  ``None`` and succeeds on a later update, once the budget allows the download.
* **Polite.** A generic User-Agent, and after a failure no request at all for a while:
  30 s, 60 s, 120 s, then 300 s for each further failure in a row; after HTTP 429 the
  time in ``Retry-After``, or 10 minutes.
"""

from __future__ import annotations

import csv
import http.client
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from zurich_air.data.provider import (
    ERROR_BACKOFF_S,
    MAX_COOLDOWN_S,
    RATE_LIMIT_COOLDOWN_S,
    USER_AGENT,
    Opener,
    _describe,
    _retry_after_s,
)

log = logging.getLogger(__name__)

VRS_ROUTES_URL = (
    "https://raw.githubusercontent.com/vradarserver/standing-data/main/routes/schema-01"
)
TTL_S = 7 * 24 * 3600.0
MAX_STALE_S = 30 * 24 * 3600.0
MAX_FILE_BYTES = 2 * 1024 * 1024  # the largest file is 246 kB today
INDEX_NAME = "index.json"
INDEX_VERSION = 1
_DATA_HEADER = b"callsign,airports\n"

WallClock = Callable[[], float]  # seconds since the epoch, like time.time

# A normalised airline callsign: ICAO airline code, then a flight number of one to four
# characters of which the last one or two may be letters (VRS route schema 1).
_AIRLINE_CALLSIGN = re.compile(r"([A-Z]{3})(\d[A-Z0-9]*)")
_FLIGHT_NUMBER = re.compile(r"\d{1,4}|\d{1,3}[A-Z]|\d{1,2}[A-Z]{2}")
_AIRPORT_CODE = re.compile(r"[A-Z0-9]{3,4}")


@dataclass(frozen=True, slots=True)
class Route:
    """The airports a flight serves, in the order flown, as ICAO codes."""

    callsign: str  # normalised, e.g. "SWR8"
    airports: tuple[str, ...]  # at least two

    @property
    def legs(self) -> tuple[tuple[str, str], ...]:
        """Consecutive (from, to) pairs: one for a direct flight, more for a multi-leg one."""
        return tuple(zip(self.airports, self.airports[1:], strict=False))


def normalise_callsign(callsign: str | None) -> str | None:
    """The callsign as VRS stores it, or ``None`` if it is not an airline flight.

    Airline callsigns are the airline's three-letter ICAO code and a flight number;
    leading zeros are dropped (``EZY0039`` -> ``EZY39``, ``EZY00AB`` -> ``EZY0AB``).
    Registrations used as callsigns by private aircraft (``HBJVK``, ``N123AB``) do not
    match, and nothing is fetched for them.
    """
    if not callsign:
        return None
    match = _AIRLINE_CALLSIGN.fullmatch(callsign.strip().upper())
    if match is None:
        return None
    code, number = match.groups()
    number = number.lstrip("0")
    if not number[:1].isdigit():
        number = "0" + number
    return code + number if _FLIGHT_NUMBER.fullmatch(number) else None


def parse_routes(text: str) -> Iterator[Route]:
    """Routes from a VRS routes CSV file; malformed rows are skipped.

    Columns are found by their headings (``Callsign``, ``AirportCodes``), as the schema
    allows new columns to be added. Raises :class:`ValueError` if they are missing.
    """
    reader = csv.reader(io.StringIO(text.removeprefix("\ufeff")))
    header = [name.strip() for name in next(reader, [])]
    try:
        cs_col, ap_col = header.index("Callsign"), header.index("AirportCodes")
    except ValueError:
        raise ValueError("not a VRS routes file") from None
    for row in reader:
        if len(row) > max(cs_col, ap_col):
            route = _route(row[cs_col], row[ap_col])
            if route is not None:
                yield route


def _route(callsign: str, airport_codes: str) -> Route | None:
    key = normalise_callsign(callsign)
    airports = tuple(airport_codes.strip().upper().split("-"))
    if key is None or len(airports) < 2:
        return None
    if not all(_AIRPORT_CODE.fullmatch(code) for code in airports):
        return None
    return Route(key, airports)


@dataclass(frozen=True, slots=True)
class _Entry:
    """What is known about one remote file."""

    fetched: float  # wall-clock time of the last successful request
    found: bool  # False: the file does not exist (HTTP 404)
    etag: str | None = None


class RouteTable:
    """Callsign -> :class:`Route` lookups backed by VRS files cached in *cache_dir*.

    *cache_dir* must be writable; it is created when the first file is stored. *clock*
    returns wall-clock seconds, because cache ages must survive restarts.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        opener: Opener = urllib.request.urlopen,
        clock: WallClock = time.time,
        base_url: str = VRS_ROUTES_URL,
        ttl_s: float = TTL_S,
        max_stale_s: float = MAX_STALE_S,
        fetch_budget: int = 2,
        budget_period_s: float = 30.0,
        timeout_s: float = 5.0,
    ) -> None:
        if fetch_budget < 0 or ttl_s <= 0 or max_stale_s < ttl_s:
            raise ValueError("invalid route table limits")
        self.cache_dir = Path(cache_dir)
        self.base_url = base_url.rstrip("/")
        self.ttl_s = ttl_s
        self.max_stale_s = max_stale_s
        self.fetch_budget = fetch_budget
        self.budget_period_s = budget_period_s
        self.timeout_s = timeout_s
        self._opener = opener
        self._clock = clock
        self._index = self._load_index()
        self._window_start: float | None = None
        self._window_used = 0
        self._cooldown_until = 0.0
        self._errors = 0
        # Counters since start, for logs and measurements.
        self.requests = 0
        self.failures = 0
        self.bytes_received = 0

    def __repr__(self) -> str:
        return f"RouteTable({str(self.cache_dir)!r})"

    def lookup(self, callsign: str | None) -> Route | None:
        """The scheduled route for *callsign*, or ``None``.

        ``None`` means: not an airline callsign, no route known, or the airline's file is
        not cached yet (it may be downloaded now, within the budget, or on a later call).
        """
        key = normalise_callsign(callsign)
        if key is None:
            return None
        code, number = key[:3], key[3:]
        # Airlines with up to 10,000 routes have one file; larger ones one per digit.
        for name in (f"{code}-all", f"{code}-{number[0]}"):
            entry = self._current(name)
            if entry is None:
                return None  # unknown for now
            if entry.found:
                return self._find(name, key)
        return None

    # -- cache -----------------------------------------------------------------------

    def _current(self, name: str) -> _Entry | None:
        """The usable entry for file *name*, refreshing it first if it is due and allowed."""
        entry = self._index.get(name)
        if (entry is None or not self._is_fresh(entry)) and self._may_fetch():
            entry = self._fetch(name) or entry
        if entry is None or self._clock() - entry.fetched > self.max_stale_s:
            return None
        return entry

    def _is_fresh(self, entry: _Entry) -> bool:
        return 0.0 <= self._clock() - entry.fetched < self.ttl_s

    def _find(self, name: str, key: str) -> Route | None:
        try:
            data = self._data_path(name).read_bytes()
        except OSError:
            self._index.pop(name, None)  # the file vanished: fetch it again later
            return None
        needle = b"\n" + key.encode("ascii") + b","
        start = data.find(needle)
        if start < 0:
            return None
        start += len(needle)
        end = data.find(b"\n", start)
        airports = data[start : end if end >= 0 else len(data)].decode("ascii", "replace")
        return _route(key, airports)

    def _data_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.csv"

    def _load_index(self) -> dict[str, _Entry]:
        try:
            raw = json.loads((self.cache_dir / INDEX_NAME).read_text(encoding="utf-8"))
            if raw.get("version") != INDEX_VERSION:
                return {}
            return {
                str(name): _Entry(float(e["fetched"]), bool(e["found"]), e.get("etag"))
                for name, e in raw["files"].items()
            }
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return {}  # no or unreadable index: start afresh, files are fetched again

    def _save_index(self) -> None:
        files = {
            name: {"fetched": e.fetched, "found": e.found, "etag": e.etag}
            for name, e in sorted(self._index.items())
        }
        payload = {"version": INDEX_VERSION, "files": files}
        _write_atomic(self.cache_dir / INDEX_NAME, json.dumps(payload, indent=1).encode())

    # -- network ---------------------------------------------------------------------

    def _may_fetch(self) -> bool:
        """Whether a request is allowed now; counts it against the budget if so."""
        now = self._clock()
        if now < self._cooldown_until:
            return False
        window = self._window_start
        if window is None or not 0.0 <= now - window < self.budget_period_s:
            self._window_start, self._window_used = now, 0
        if self._window_used >= self.fetch_budget:
            return False
        self._window_used += 1
        return True

    def _fetch(self, name: str) -> _Entry | None:
        """Download file *name* and update the cache; ``None`` if the request failed."""
        old = self._index.get(name)
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        if old is not None and old.found and old.etag and self._data_path(name).is_file():
            headers["If-None-Match"] = old.etag
        request = urllib.request.Request(f"{self.base_url}/{name[0]}/{name}.csv", headers=headers)
        self.requests += 1
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                body = response.read(MAX_FILE_BYTES + 1)
                encoding = response.headers.get("Content-Encoding", "")
                etag = response.headers.get("ETag")
            self.bytes_received += len(body)
            data, count = _normalise(_decode(body, encoding))
            _write_atomic(self._data_path(name), data)
            entry = _Entry(self._clock(), True, etag)
            log.debug("routes: %s: %d routes, %d bytes", name, count, len(body))
        except urllib.error.HTTPError as exc:
            retry_after = _retry_after_s(exc.headers.get("Retry-After") if exc.headers else None)
            if exc.code == 304 and old is not None:  # unchanged since the last download
                entry = _Entry(self._clock(), old.found, old.etag)
            elif exc.code in (404, 410):  # no such airline (or not split this way)
                entry = _Entry(self._clock(), False)
                self._data_path(name).unlink(missing_ok=True)
            elif exc.code == 429:
                cooldown = RATE_LIMIT_COOLDOWN_S if retry_after is None else retry_after
                self._cool_down(cooldown, "HTTP 429 rate limited")
                return None
            else:
                self._back_off(f"HTTP {exc.code}", retry_after)
                return None
        except (OSError, http.client.HTTPException, ValueError, zlib.error) as exc:
            self._back_off(_describe(exc))
            return None
        self._errors = 0
        self._index[name] = entry
        try:
            self._save_index()
        except OSError as exc:  # the entry still counts for this run
            log.warning("routes: cannot write the cache index: %s", _describe(exc))
        return entry

    def _back_off(self, reason: str, retry_after: float | None = None) -> None:
        self._errors += 1
        delay = ERROR_BACKOFF_S[min(self._errors, len(ERROR_BACKOFF_S)) - 1]
        self._cool_down(max(delay, retry_after or 0.0), reason)

    def _cool_down(self, seconds: float, reason: str) -> None:
        self.failures += 1
        seconds = min(seconds, MAX_COOLDOWN_S)
        self._cooldown_until = self._clock() + seconds
        log.warning("routes: %s; next try in %.0f s", reason, seconds)


def _decode(body: bytes, content_encoding: str) -> str:
    """The text of a response body, gunzipped if needed, at most ``MAX_FILE_BYTES``."""
    if len(body) > MAX_FILE_BYTES:
        raise ValueError("response too large")
    if content_encoding.strip().lower() == "gzip":
        inflater = zlib.decompressobj(wbits=31)  # gzip container
        body = inflater.decompress(body, MAX_FILE_BYTES + 1)
        if len(body) > MAX_FILE_BYTES or not inflater.eof:
            raise ValueError("compressed response too large or truncated")
    return body.decode("utf-8")


def _normalise(text: str) -> tuple[bytes, int]:
    """The cache file for a VRS routes CSV: one ``callsign,airports`` line per route."""
    lines = [_DATA_HEADER]
    seen: set[str] = set()
    for route in parse_routes(text):
        if route.callsign not in seen:  # the first of two spellings wins, as in VRS
            seen.add(route.callsign)
            lines.append(f"{route.callsign},{'-'.join(route.airports)}\n".encode("ascii"))
    return b"".join(lines), len(seen)


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace *path* in one step, so a power cut never leaves half a file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
