"""The bundled OurAirports data, its lookups and the script that regenerates it (offline)."""

from __future__ import annotations

import csv
import gzip
import importlib.util
import logging
import sys
from importlib import resources
from pathlib import Path

import pytest

from zurich_air.config import ZRH_RUNWAYS, AirportConfig
from zurich_air.data import airports
from zurich_air.data.airports import Airport, AirportTable, airport_position, lookup
from zurich_air.processing.geo import distance_km

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update-airports.py"
DATA_DIR = Path(str(resources.files("zurich_air") / "assets" / "airports"))
DATA_FILES = ("airports.csv.gz", "runways.csv.gz")
MAX_DATA_BYTES = 400_000

# Airport reference points published by the airports' operators (degrees, minutes, seconds
# converted), not taken from OurAirports.
KNOWN_POSITIONS = {
    "LSZH": ("ZRH", 47.4647, 8.5492),
    "EGLL": ("LHR", 51.4775, -0.4614),
    "KJFK": ("JFK", 40.6397, -73.7789),
    "OMDB": ("DXB", 25.2528, 55.3644),
}


# --- The bundled data ------------------------------------------------------------------


def test_zurich_is_bundled_with_its_three_runways():
    zrh = lookup("LSZH")
    assert zrh is not None
    assert (zrh.icao, zrh.iata, zrh.display_code, zrh.country) == ("LSZH", "ZRH", "ZRH", "CH")
    assert [(r.le_ident, r.he_ident) for r in zrh.runways] == [
        ("10", "28"),
        ("14", "32"),
        ("16", "34"),
    ]


def test_zurich_runway_ends_match_the_configured_thresholds():
    zrh = lookup("LSZH")
    assert zrh is not None and len(zrh.runways) == len(ZRH_RUNWAYS)
    for runway, (lat1, lon1, lat2, lon2) in zip(zrh.runways, ZRH_RUNWAYS, strict=True):
        assert distance_km(runway.le_lat, runway.le_lon, lat1, lon1) * 1000 < 1.0
        assert distance_km(runway.he_lat, runway.he_lon, lat2, lon2) * 1000 < 1.0
        assert runway.length_ft is not None and runway.length_ft > 8000


def test_zurich_matches_the_configured_airport():
    default = AirportConfig()
    zrh = lookup("LSZH")
    assert zrh is not None and zrh.elevation_ft is not None
    assert distance_km(zrh.lat, zrh.lon, default.latitude, default.longitude) < 0.01
    assert abs(zrh.elevation_ft - default.elevation_ft) <= 5


@pytest.mark.parametrize("icao", sorted(KNOWN_POSITIONS))
def test_well_known_airports(icao):
    iata, lat, lon = KNOWN_POSITIONS[icao]
    airport = lookup(icao)
    assert airport is not None
    assert airport.display_code == iata
    assert airport.runways
    position = airport_position(icao)
    assert position == (airport.lat, airport.lon)
    assert distance_km(*position, lat, lon) < 2.0


@pytest.mark.parametrize("code", ["XXXX", "ZRH", "", "   ", "LSZ", "LSZHX"])
def test_unknown_codes(code):
    assert lookup(code) is None
    assert airport_position(code) is None


def test_lookup_ignores_case_and_surrounding_space():
    zrh = lookup("LSZH")
    assert zrh is not None
    assert lookup("lszh") is zrh
    assert lookup(" LsZh\n") is zrh
    assert airport_position("lszh") == (zrh.lat, zrh.lon)


def test_every_bundled_airport_parses():
    table = AirportTable(DATA_DIR)
    codes = table.codes()
    assert 3000 < len(codes) < 5000
    for code in codes:
        airport = table.lookup(code)
        assert airport is not None and airport.icao == code
        assert -90 <= airport.lat <= 90 and -180 <= airport.lon <= 180
        assert airport.name and len(airport.country) == 2
        for runway in airport.runways:
            assert runway.le_ident and runway.he_ident
            # Both ends belong to this airport and are distinct points.
            assert distance_km(airport.lat, airport.lon, runway.le_lat, runway.le_lon) <= 10
            assert distance_km(airport.lat, airport.lon, runway.he_lat, runway.he_lon) <= 10
            assert distance_km(runway.le_lat, runway.le_lon, runway.he_lat, runway.he_lon) >= 0.1


def test_data_files_are_small_and_deterministic():
    sizes = {name: (DATA_DIR / name).stat().st_size for name in DATA_FILES}
    assert sum(sizes.values()) < MAX_DATA_BYTES, sizes
    for name in DATA_FILES:
        raw = (DATA_DIR / name).read_bytes()
        assert raw[:3] == b"\x1f\x8b\x08"  # gzip, deflate
        assert raw[3] == 0, "no file name or other optional header fields"
        assert raw[4:8] == bytes(4), "no time stamp"
        rows = list(csv.reader(gzip.decompress(raw).decode("utf-8").splitlines()))
        assert rows[1:] == sorted(rows[1:]), f"{name} is not sorted"
    airport_lines = gzip.decompress((DATA_DIR / "airports.csv.gz").read_bytes()).splitlines()
    codes = [line.split(b",", 1)[0] for line in airport_lines[1:]]
    assert len(codes) == len(set(codes))


def test_data_records_its_source():
    note = (DATA_DIR / "SOURCE.txt").read_text(encoding="utf-8")
    assert "OurAirports" in note and "public domain" in note
    assert note.count("sha256") == 2
    assert "Generated: 20" in note


def test_unreadable_data_is_logged_and_finds_nothing(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(airports.resources, "files", lambda package: tmp_path)
    airports._bundled.cache_clear()
    try:
        with caplog.at_level(logging.ERROR, logger="zurich_air.data.airports"):
            assert lookup("LSZH") is None
            assert lookup("EGLL") is None
        assert len(caplog.records) == 1  # logged once, not on every lookup
    finally:
        airports._bundled.cache_clear()


def test_a_file_in_another_format_is_refused(tmp_path):
    for name in DATA_FILES:
        (tmp_path / name).write_bytes(gzip.compress(b"id,ident,name\n1,LSZH,Zurich\n"))
    with pytest.raises(ValueError, match="unexpected columns"):
        AirportTable(tmp_path)


# --- scripts/update-airports.py --------------------------------------------------------

# The OurAirports files have more columns; these are the ones the script reads.
AIRPORTS_CSV = """\
id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,iso_country,municipality,\
scheduled_service,icao_code,iata_code
1,LSZH,large_airport,Zürich Airport,47.458056,8.548056,1417,CH,Zurich,yes,LSZH,ZRH
2,XOLD,medium_airport,"Big, ""Busy""
  Airport",-33.5000004,-0.0000001,,AU,,yes,XNEW,BBY
3,XSML,small_airport,Small Field,46.1,7.1,1500,CH,Sion,no,,
4,AR-0767,medium_airport,Placeholder,-26.9,-54.0,,AR,,yes,,
"""
RUNWAYS_CSV = """\
airport_ref,length_ft,surface,closed,le_ident,le_latitude_deg,le_longitude_deg,\
he_ident,he_latitude_deg,he_longitude_deg
1,8202,CON,0,10,47.458900451660156,8.537469863891602,28,47.4566,8.57045
1,1010,GRASS,0,01H,47.449333,8.54772,19H,47.451965,8.548985
1,3000,ASP,1,03,47.45,8.54,21,47.46,8.55
1,5000,ASP,0,05,,,23,,
1,6070,ASP,0,06,47.45,8.54,24,47.46,9.24
1,9000,ASP,0,07,47.45,8.54,25,47.45,8.54
2,9000,ASP,0,07,-33.51,-0.01,25,-33.49,0.01
2,3000,WATER,0,04W,-33.51,-0.01,22W,-33.49,0.01
3,2000,GRASS,0,09,46.1,7.09,27,46.1,7.11
"""


@pytest.fixture(scope="module")
def script():
    """The script as a module (its file name is not importable)."""
    spec = importlib.util.spec_from_file_location("update_airports", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        del sys.modules[spec.name]


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "airports.csv").write_text(AIRPORTS_CSV, encoding="utf-8")
    (directory / "runways.csv").write_text(RUNWAYS_CSV, encoding="utf-8")
    return directory


def test_script_filters_and_rounds(script, source, tmp_path):
    out = tmp_path / "out"
    assert script.main(["--source", str(source), "--out", str(out)]) == 0
    table = AirportTable(out)
    assert table.codes() == ["LSZH", "XNEW"]  # neither the small field nor the placeholder

    zrh = table.lookup("LSZH")
    assert zrh is not None
    # Closed and helicopter runways are left out, and those with an end missing, an end
    # 50 km away or both ends at the same point.
    assert [(r.le_ident, r.he_ident) for r in zrh.runways] == [("10", "28")]
    assert (zrh.runways[0].le_lat, zrh.runways[0].le_lon) == (47.4589, 8.53747)

    big = table.lookup("XNEW")
    assert big == Airport(
        icao="XNEW",
        iata="BBY",
        name='Big, "Busy" Airport',
        municipality=None,
        country="AU",
        lat=-33.5,
        lon=0.0,
        elevation_ft=None,
        runways=big.runways,
    )
    assert [(r.le_ident, r.he_ident) for r in big.runways] == [("07", "25")]  # no water lane
    assert table.lookup("xold") is big  # the older ident finds it too
    assert "-0," not in gzip.decompress((out / "airports.csv.gz").read_bytes()).decode()


def test_script_output_is_byte_identical_on_a_rerun(script, source, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    assert script.main(["--source", str(source), "--out", str(first)]) == 0
    assert script.main(["--source", str(source), "--out", str(second)]) == 0
    for name in DATA_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_script_includes_airports_on_request(script, source, tmp_path):
    out = tmp_path / "out"
    assert script.main(["--source", str(source), "--out", str(out), "--include", "xsml"]) == 0
    small = AirportTable(out).lookup("XSML")
    assert small is not None and small.display_code == "XSML" and small.iata is None
    assert len(small.runways) == 1


def test_script_fails_without_an_airport_it_must_include(script, source, tmp_path, capsys):
    out = tmp_path / "out"
    assert script.main(["--source", str(source), "--out", str(out), "--include", "ZZZZ"]) == 1
    assert "ZZZZ" in capsys.readouterr().err
    (source / "airports.csv").write_text(AIRPORTS_CSV.replace("LSZH", "LSZX"), encoding="utf-8")
    assert script.main(["--source", str(source), "--out", str(out)]) == 1
    assert "LSZH" in capsys.readouterr().err
    assert not out.exists()
