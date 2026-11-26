"""Airports and their runways, looked up by ICAO code in bundled OurAirports data.

``assets/airports`` holds the airports with scheduled service that OurAirports classifies
as large or medium (about 3,300, Zürich always among them) and their open runways, as two
small gzip-compressed CSV files with one line per row. ``scripts/update-airports.py``
regenerates them, and ``SOURCE.txt`` beside them records when and from which files.
OurAirports data is in the public domain.

The files are read on the first lookup, which only indexes their lines by ICAO code; an
airport's line is parsed when that airport is first asked for. This keeps the start
quick on a Raspberry Pi Zero. If the files cannot be read, the error is logged once and
every lookup returns ``None``: a missing airport must never stop the display.
"""

from __future__ import annotations

import csv
import gzip
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from importlib.resources.abc import Traversable

log = logging.getLogger(__name__)

AIRPORT_COLUMNS = (
    "icao",
    "ident",  # an older OurAirports ident that also finds the airport, if any
    "iata",
    "name",
    "municipality",
    "country",
    "lat",
    "lon",
    "elevation_ft",
)
RUNWAY_COLUMNS = (
    "icao",
    "le_ident",
    "le_lat",
    "le_lon",
    "he_ident",
    "he_lat",
    "he_lon",
    "length_ft",
)


@dataclass(frozen=True, slots=True)
class Runway:
    """A runway between its two ends, in WGS84 degrees.

    ``le`` is the end with the lower number (``10`` of runway 10/28), ``he`` the other.
    """

    le_ident: str
    le_lat: float
    le_lon: float
    he_ident: str
    he_lat: float
    he_lon: float
    length_ft: int | None = None


@dataclass(frozen=True, slots=True)
class Airport:
    icao: str
    iata: str | None
    name: str
    municipality: str | None
    country: str  # ISO 3166-1 alpha-2
    lat: float  # the airport reference point
    lon: float
    elevation_ft: int | None
    runways: tuple[Runway, ...] = ()

    @property
    def display_code(self) -> str:
        """The code a passenger knows: IATA (``ZRH``) if the airport has one, else ICAO."""
        return self.iata or self.icao


def lookup(icao: str) -> Airport | None:
    """The airport with the ICAO code *icao* (in any case), or None if it is not bundled."""
    table = _bundled()
    return None if table is None else table.lookup(icao)


def airport_position(icao: str) -> tuple[float, float] | None:
    """(lat, lon) of the airport with the ICAO code *icao*, or None if it is not bundled."""
    airport = lookup(icao)
    return None if airport is None else (airport.lat, airport.lon)


class AirportTable:
    """The airports in a directory holding ``airports.csv.gz`` and ``runways.csv.gz``.

    Raises OSError, EOFError or ValueError if the files are missing or not in this format.
    """

    def __init__(self, directory: Traversable) -> None:
        by_code: dict[str, str] = {}
        by_ident: dict[str, str] = {}
        for line in _lines(directory / "airports.csv.gz", AIRPORT_COLUMNS):
            icao, ident, _ = line.split(",", 2)  # neither is ever quoted
            by_code[icao] = line
            if ident:
                by_ident[ident] = line
        self._lines = by_ident | by_code  # an ICAO code wins over another airport's old ident
        self._runway_lines: dict[str, list[str]] = {}
        for line in _lines(directory / "runways.csv.gz", RUNWAY_COLUMNS):
            self._runway_lines.setdefault(line.partition(",")[0], []).append(line)
        self._parsed: dict[str, Airport] = {}

    def codes(self) -> list[str]:
        """The ICAO codes of all airports, sorted."""
        return sorted({line.partition(",")[0] for line in self._lines.values()})

    def lookup(self, icao: str) -> Airport | None:
        """The airport with the ICAO code (or older ident) *icao*, in any case."""
        if not isinstance(icao, str):
            return None
        line = self._lines.get(icao.strip().upper())
        if line is None:
            return None
        code = line.partition(",")[0]
        airport = self._parsed.get(code)
        if airport is None:
            airport = self._parsed[code] = self._parse(line)
        return airport

    def _parse(self, line: str) -> Airport:
        icao, _, iata, name, municipality, country, lat, lon, elevation = _fields(
            line, AIRPORT_COLUMNS
        )
        runways = []
        for runway_line in self._runway_lines.get(icao, ()):
            _, le_ident, le_lat, le_lon, he_ident, he_lat, he_lon, length = _fields(
                runway_line, RUNWAY_COLUMNS
            )
            runways.append(
                Runway(
                    le_ident,
                    float(le_lat),
                    float(le_lon),
                    he_ident,
                    float(he_lat),
                    float(he_lon),
                    int(length) if length else None,
                )
            )
        return Airport(
            icao,
            iata or None,
            name,
            municipality or None,
            country,
            float(lat),
            float(lon),
            int(elevation) if elevation else None,
            tuple(runways),
        )


@cache
def _bundled() -> AirportTable | None:
    directory = resources.files("zurich_air") / "assets" / "airports"
    try:
        return AirportTable(directory)
    except (OSError, EOFError, ValueError) as exc:
        log.error("airport data could not be read from %s: %s", directory, exc)
        return None


def _lines(path: Traversable, columns: Sequence[str]) -> list[str]:
    """The data lines of a gzip-compressed CSV file whose header is *columns*."""
    lines = gzip.decompress(path.read_bytes()).decode("utf-8").split("\n")
    if lines[0] != ",".join(columns):
        raise ValueError(f"{path.name}: unexpected columns {lines[0]!r}")
    return [line for line in lines[1:] if line]


def _fields(line: str, columns: Sequence[str]) -> list[str]:
    fields = next(csv.reader([line]))
    if len(fields) != len(columns):
        raise ValueError(f"expected {len(columns)} fields: {line!r}")
    return fields
