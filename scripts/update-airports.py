"""Regenerate the bundled airport data in src/zurich_air/assets/airports from OurAirports.

    uv run python scripts/update-airports.py [--include ICAO ...] [--source DIR] [--out DIR]

Downloads airports.csv and runways.csv from OurAirports (public domain) and keeps the
airports with scheduled service of type ``large_airport`` or ``medium_airport``, plus any
named with ``--include`` (Zürich, LSZH, always). Each airport is keyed by its ICAO code
(``icao_code``, else the OurAirports ``ident`` when that looks like one); an older ident
is kept alongside, so that it still finds the airport. Its runways are those that are
open, have coordinates at both ends and are neither helicopter nor water lanes; a few
whose ends are mistyped in the source (an end far from the airport, or both ends at the
same point) are left out. Coordinates are rounded to 6 decimals (about 0.1 m).

The output is two small gzip-compressed CSV files with sorted rows, one line per row, and
a gzip header without a time stamp or file name, so that running this twice on the same
input writes identical files; and SOURCE.txt with the date, the source URLs and the
SHA-256 of the files read. ``--source DIR`` reads airports.csv and runways.csv from DIR
instead of downloading them.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import re
import sys
import urllib.request
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from zurich_air import __version__
from zurich_air.data.airports import AIRPORT_COLUMNS, RUNWAY_COLUMNS
from zurich_air.processing.geo import distance_km

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "src" / "zurich_air" / "assets" / "airports"
BASE_URL = "https://davidmegginson.github.io/ourairports-data/"
SOURCE_FILES = ("airports.csv", "runways.csv")
DATA_FILES = ("airports.csv.gz", "runways.csv.gz")
USER_AGENT = f"zurich-air/{__version__}"  # deliberately generic, as in data/provider.py
TIMEOUT_S = 60.0
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024

AIRPORT_TYPES = frozenset({"large_airport", "medium_airport"})
ALWAYS_INCLUDE = ("LSZH",)  # the default airport must never drop out
ICAO_PATTERN = re.compile(r"[A-Z0-9]{4}")
HELICOPTER_RUNWAY = re.compile(r"^H|H$")  # "01H" at LSZH, "H09L" at Oakey
COORDINATE_DECIMALS = 6
MAX_END_DISTANCE_KM = 10.0  # from the airport reference point to either runway end
MIN_RUNWAY_KM = 0.1  # between the two ends

Row = tuple[str, ...]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--include",
        nargs="+",
        default=[],
        metavar="ICAO",
        help="also keep these airports, whatever their type (matched on ICAO code or ident)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        metavar="DIR",
        help="read airports.csv and runways.csv from DIR instead of downloading them",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    args = parser.parse_args(argv)

    raw = {name: read_source(name, args.source) for name in SOURCE_FILES}
    include = {code.strip().upper() for code in (*ALWAYS_INCLUDE, *args.include)}
    try:
        airports, runways = build(decode(raw["airports.csv"]), decode(raw["runways.csv"]), include)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc!r}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "airports.csv.gz").write_bytes(gzip_csv(AIRPORT_COLUMNS, airports))
    (args.out / "runways.csv.gz").write_bytes(gzip_csv(RUNWAY_COLUMNS, runways))
    note = source_note(raw, len(airports), len(runways), sorted(include))
    (args.out / "SOURCE.txt").write_text(note, encoding="utf-8")
    size = sum((args.out / name).stat().st_size for name in DATA_FILES)
    print(f"{len(airports)} airports, {len(runways)} runways, {size / 1000:.0f} kB in {args.out}")
    return 0


def read_source(name: str, source_dir: Path | None) -> bytes:
    if source_dir is not None:
        return (source_dir / name).read_bytes()
    request = urllib.request.Request(BASE_URL + name, headers={"User-Agent": USER_AGENT})
    print(f"downloading {BASE_URL + name}", file=sys.stderr)
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        body = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise SystemExit(f"error: {name} is larger than {MAX_DOWNLOAD_BYTES} bytes")
    return body


def decode(body: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"), newline="")))


def build(
    airport_rows: Iterable[dict[str, str]],
    runway_rows: Iterable[dict[str, str]],
    include: set[str],
) -> tuple[list[Row], list[Row]]:
    """The airport and runway rows to bundle, each sorted. Raises ValueError on bad input."""
    by_ref: dict[str, tuple[str, float, float]] = {}  # OurAirports id → ICAO code, position
    airports: dict[str, Row] = {}
    for row in airport_rows:
        ident = row["ident"].strip().upper()
        icao = row["icao_code"].strip().upper() or ident
        included = ident in include or icao in include
        wanted = row["type"] in AIRPORT_TYPES and row["scheduled_service"] == "yes"
        if not (included or (wanted and ICAO_PATTERN.fullmatch(icao))):
            continue  # this also drops placeholders such as "AR-0767" that no route names
        if icao in airports:
            raise ValueError(f"two airports with the ICAO code {icao}")
        lat, lon = coordinate(row["latitude_deg"]), coordinate(row["longitude_deg"])
        by_ref[row["id"]] = (icao, float(lat), float(lon))
        airports[icao] = (
            icao,
            ident if ident != icao and ICAO_PATTERN.fullmatch(ident) else "",
            row["iata_code"].strip().upper(),
            text(row["name"]),
            text(row["municipality"]),
            row["iso_country"].strip().upper(),
            lat,
            lon,
            integer(row["elevation_ft"]),
        )
    # An older ident keeps finding its airport, unless it is another airport's ICAO code.
    for icao, row in airports.items():
        if row[1] in airports:
            airports[icao] = (row[0], "", *row[2:])
    missing = sorted(include - set(airports) - {row[1] for row in airports.values()})
    if missing:
        raise ValueError(f"not found in the source: {', '.join(missing)}")

    runways: list[Row] = []
    for row in runway_rows:
        airport = by_ref.get(row["airport_ref"])
        if airport is None or row["closed"].strip() == "1":
            continue
        icao, lat, lon = airport
        ends = [
            row[f"{end}_{axis}_deg"].strip()
            for end in ("le", "he")
            for axis in ("latitude", "longitude")
        ]
        if not all(ends):
            continue
        le_ident, he_ident = row["le_ident"].strip().upper(), row["he_ident"].strip().upper()
        if HELICOPTER_RUNWAY.search(le_ident) or HELICOPTER_RUNWAY.search(he_ident):
            continue
        if row["surface"].strip().upper().startswith("WAT"):  # seaplane lanes
            continue
        le_lat, le_lon, he_lat, he_lon = (coordinate(value) for value in ends)
        if not plausible(lat, lon, *(float(value) for value in (le_lat, le_lon, he_lat, he_lon))):
            continue
        runways.append(
            (icao, le_ident, le_lat, le_lon, he_ident, he_lat, he_lon, integer(row["length_ft"]))
        )
    return sorted(airports.values()), sorted(runways)


def plausible(lat: float, lon: float, *ends: float) -> bool:
    """Whether runway ends (le_lat, le_lon, he_lat, he_lon) fit an airport at (lat, lon)."""
    le_lat, le_lon, he_lat, he_lon = ends
    return (
        distance_km(lat, lon, le_lat, le_lon) <= MAX_END_DISTANCE_KM
        and distance_km(lat, lon, he_lat, he_lon) <= MAX_END_DISTANCE_KM
        and distance_km(le_lat, le_lon, he_lat, he_lon) >= MIN_RUNWAY_KM
    )


def text(value: str) -> str:
    """*value* with every run of white space, line breaks included, as a single space."""
    return " ".join(value.split())


def coordinate(value: str) -> str:
    """*value* in degrees, rounded to 6 decimals, without trailing zeros."""
    number = round(float(value), COORDINATE_DECIMALS)
    digits = f"{number:.{COORDINATE_DECIMALS}f}".rstrip("0").rstrip(".")
    return "0" if digits == "-0" else digits


def integer(value: str) -> str:
    value = value.strip()
    return str(round(float(value))) if value else ""


def gzip_csv(header: Sequence[str], rows: Iterable[Row]) -> bytes:
    """CSV with Unix line endings, gzip-compressed with neither time stamp nor file name."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, compresslevel=9, mtime=0) as fh:
        fh.write(buffer.getvalue().encode("utf-8"))
    return compressed.getvalue()


def source_note(raw: dict[str, bytes], airports: int, runways: int, include: list[str]) -> str:
    lines = [
        "Airport and runway data for zurich_air.data.airports",
        "",
        "Source:    OurAirports, https://ourairports.com/data/",
        "Licence:   public domain (the ourairports-data repository uses the Unlicense)",
        f"Generated: {datetime.now(UTC).date().isoformat()} by scripts/update-airports.py",
        "",
        *(f"{BASE_URL}{name}  sha256 {hashlib.sha256(raw[name]).hexdigest()}" for name in raw),
        "",
        f"airports.csv.gz: {airports} airports, large or medium with scheduled service,",
        f"                 plus {', '.join(include)}",
        f"runways.csv.gz:  {runways} runways, open, with both ends, no helicopter or water lanes,",
        f"                 ends within {MAX_END_DISTANCE_KM:g} km of the airport",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
