"""Deterministic synthetic traffic scenes for visual development and tests.

Nothing here is ever used by the live data path — this module is imported only by the
``preview`` command and the test suite.

The scenes are modelled on real Zürich operations (ILS 14 arrivals from the north-west,
departures off runways 16 and 28, high-level overflights, a Rega helicopter), but every
flight is invented: callsigns use real airline ICAO prefixes followed by a running
three-digit number (SWR001, UAE002, …), and registrations follow an ``HB-?X?`` pattern.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from zurich_air.config import ZRH_RUNWAYS, Config
from zurich_air.data.aircraft import Aircraft, TrafficSnapshot
from zurich_air.processing.geo import KM_PER_NM, destination

TIMESTAMP = datetime(2026, 12, 24, 15, 42, tzinfo=ZoneInfo("Europe/Zurich"))
SCENE_NAMES = ("typical", "busy", "quiet", "empty", "degraded", "stale")
TRAIL_INTERVAL_S = 60

_DEFAULTS = Config()
CITY = (_DEFAULTS.location.latitude, _DEFAULTS.location.longitude)
AIRPORT = (_DEFAULTS.airport.latitude, _DEFAULTS.airport.longitude)
_RWY_10_28, _RWY_14_32, _RWY_16_34 = ZRH_RUNWAYS
THR_14 = _RWY_14_32[:2]  # north-western end
END_16 = _RWY_16_34[2:]  # southern end, where runway 16 departures lift off
END_28 = _RWY_10_28[:2]  # western end, where runway 28 departures lift off
FINAL_14_DEG = 137.0
FIELD_ELEVATION_FT = 1416


@dataclass(frozen=True)
class SyntheticScene:
    name: str
    snapshot: TrafficSnapshot
    trails: dict[str, tuple[tuple[float, float], ...]]
    now: datetime  # wall-clock time of the render; later than the data for "stale"


@dataclass(frozen=True)
class _Flight:
    """A synthetic flight, placed *dist_km* from *ref* on *bearing*."""

    callsign: str | None
    type_code: str | None
    ref: tuple[float, float]
    bearing: float
    dist_km: float
    altitude_ft: int | None
    track_deg: float | None
    ground_speed_kt: float | None
    vertical_rate_fpm: int | None = 0
    turn_deg_per_min: float = 0.0  # bends the trail for aircraft that are turning
    trail: int = 5  # number of past positions
    extra: dict = field(default_factory=dict)  # any other Aircraft fields


def _final(callsign: str, type_code: str, dist_km: float, speed_kt: float) -> _Flight:
    """An arrival established on the ILS 14 (3° glide path) *dist_km* from the threshold."""
    altitude = FIELD_ELEVATION_FT + round(dist_km * 1000 * 0.0524 / 0.3048, -2)
    return _Flight(
        callsign,
        type_code,
        THR_14,
        FINAL_14_DEG + 180,
        dist_km,
        int(altitude),
        FINAL_14_DEG,
        speed_kt,
        -int(round(speed_kt * 5.3, -1)),
    )


_TYPICAL = (
    # Arrivals: ILS 14 from the north-west, feeders from the north and east.
    _final("UAE001", "A388", 9, 145),
    _final("SWR002", "BCS3", 20, 170),
    _Flight("DLH003", "A20N", THR_14, 332, 33, 7000, 112, 210, -900, turn_deg_per_min=8),
    _Flight("EDW004", "A320", AIRPORT, 8, 42, 11_000, 196, 290, -1800),
    _Flight("BAW005", "A320", AIRPORT, 83, 40, 12_000, 262, 300, -1600),
    _Flight("SWR006", "A333", AIRPORT, 62, 63, 14_000, 248, 320, -1500),
    _Flight("EZY007", "A319", AIRPORT, 292, 47, 9_000, 118, 260, -1200),
    # Departures: runway 16 to the south, runway 28 to the west.
    _Flight("SWR008", "B77W", END_16, 172, 9, 5_900, 208, 220, 2600, turn_deg_per_min=18),
    _Flight("OAW009", "E295", END_16, 176, 27, 11_000, 178, 280, 2100),
    _Flight("EZS010", "A20N", END_28, 272, 11, 5_600, 268, 210, 2300),
    _Flight("KLM011", "E190", END_28, 288, 33, 13_000, 302, 300, 1800, turn_deg_per_min=6),
    # High-level overflights.
    _Flight("THY012", "A21N", CITY, 300, 56, 37_000, 102, 470),
    _Flight("RYR013", "B38M", CITY, 42, 50, 39_000, 226, 455),
    _Flight("WZZ014", "A21N", CITY, 222, 64, 35_000, 64, 480, 64),
    _Flight("AFR015", "A320", CITY, 196, 36, 34_000, 33, 440),
    _Flight("QTR016", "B789", CITY, 335, 71, 40_000, 133, 505),
    _Flight("DLH017", "A321", CITY, 118, 67, 32_000, 296, 430, -64),
    _Flight("AUA018", "A320", CITY, 78, 27, 36_000, 276, 450),
    _Flight("ETD019", "B789", CITY, 262, 42, 38_000, 121, 495),
    # Rega helicopter, a business jet and a light aircraft.
    _Flight("SAZ020", "EC45", CITY, 150, 13, 2_600, 204, 125, -200, trail=4),
    _Flight(
        "HBJXA", "GLF6", CITY, 246, 23, 8_100, 58, 260, -1500, extra={"registration": "HB-JXA"}
    ),
    _Flight(
        "HBCXB", "C172", CITY, 228, 31, 3_500, 236, 95, 0, trail=4,
        extra={"registration": "HB-CXB"},
    ),
)  # fmt: skip

_QUIET = (
    _final("SWR001", "A20N", 14, 160),
    _Flight("THY002", "A21N", CITY, 300, 48, 37_000, 102, 470),
    _Flight("SAZ003", "EC45", CITY, 150, 13, 2_600, 204, 125, -200, trail=4),
)

# Busy: typical traffic plus a longer arrival queue, more departures and overflights.
_BUSY_EXTRA = (
    _final("LOT023", "E195", 29, 180),
    _final("CLH024", "CRJ9", 36, 200),
    _Flight("SWR025", "A21N", THR_14, 340, 44, 9_000, 120, 240, -1100, turn_deg_per_min=6),
    _Flight("DLA026", "E190", AIRPORT, 25, 55, 13_000, 205, 310, -1700),
    _Flight("AEE027", "A20N", AIRPORT, 95, 58, 15_000, 268, 330, -1400),
    _Flight("SWR028", "A320", END_16, 168, 16, 8_300, 190, 250, 2400, turn_deg_per_min=10),
    _Flight("EDW029", "A339", END_28, 270, 22, 9_800, 262, 270, 2000),
    _Flight("GEC030", "B77L", END_28, 300, 52, 19_000, 315, 380, 1500),
)
_BUSY_AIRLINES = (
    ("BAW", "A320"), ("KLM", "B738"), ("DLH", "A20N"), ("AFR", "A20N"), ("IBE", "A321"),
    ("VLG", "A320"), ("SAS", "A20N"), ("FIN", "A321"), ("UAL", "B763"), ("DAL", "A339"),
    ("ACA", "B788"), ("SIA", "A359"), ("CPA", "A35K"), ("PGT", "B38M"), ("TAP", "A21N"),
)  # fmt: skip


def _busy_overflights(first_number: int) -> tuple[_Flight, ...]:
    rng = random.Random(2412)
    flights = []
    for i, (prefix, type_code) in enumerate(_BUSY_AIRLINES):
        flights.append(
            _Flight(
                f"{prefix}{first_number + i:03d}",
                type_code,
                CITY,
                rng.uniform(0, 360),
                rng.uniform(15, 76),
                rng.randrange(300, 410, 10) * 100,
                round(rng.uniform(0, 360)),
                rng.uniform(420, 510),
                rng.choice((0, 0, 64, -64)),
            )
        )
    return tuple(flights)


# Degraded: incomplete and odd data, as real ADS-B feeds produce it.
_DEGRADED = (
    _final("SWR001", "A20N", 12, 160),
    _Flight(None, None, CITY, 20, 18, 4_200, 300, 180, 900, extra={"registration": "HB-JXC"}),
    _Flight(None, None, AIRPORT, 100, 9, None, None, None, None),  # position only
    _Flight("SWR004", "B77W", AIRPORT, 300, 26, None, 92, 250, None),  # no altitude
    _Flight("XAX005", "ZZZZ", CITY, 330, 45, 51_000, 140, 520, 6000),  # unknown codes
    _Flight("EZS90ZZ", "A20N", CITY, 160, 40, -50, 359.9, 30, -5000),  # odd values
    _Flight("DLH007", "A320", AIRPORT, 120, 1, 1_400, 280, 15, 0, extra={"on_ground": True}),
    _Flight(
        "SWR008", "A320", CITY, 200, 22, 9_000, 211, 260, -2500,
        extra={"squawk": "7700", "emergency": True},
    ),
    _Flight("  ", "A21N", CITY, 60, 60, 24_000, 725.0, 400, 0, extra={"registration": ""}),
    _Flight("ABCDEFGHIJK", "LUFTHANSA-LONG", AIRPORT, 250, 12, 3_800, 45, 180, 0),  # too long
    _Flight("SUI10", "F18", CITY, 280, 35, 18_000, 180, 380, 0, extra={"military": True}),
    _Flight("EZY012", None, CITY, 15, 70, 33_000, None, 440, 0, extra={"position_age_s": 45.0}),
    _Flight("WZZ013", "A21N", CITY, 95, 79.9, 36_000, 270, 450, 0),  # at the edge of range
)  # fmt: skip


def scene(name: str) -> SyntheticScene:
    """Build the synthetic scene *name* (one of :data:`SCENE_NAMES`)."""
    now = TIMESTAMP
    timestamp = TIMESTAMP
    if name == "typical":
        flights = _TYPICAL
    elif name == "busy":
        flights = _TYPICAL + _BUSY_EXTRA + _busy_overflights(len(_TYPICAL) + len(_BUSY_EXTRA) + 1)
    elif name == "quiet":
        flights = _QUIET
    elif name == "empty":
        flights = ()
    elif name == "degraded":
        flights = _DEGRADED
    elif name == "stale":
        flights = _TYPICAL
        timestamp = TIMESTAMP - timedelta(minutes=11)
    else:
        raise ValueError(f"unknown scene {name!r}; choose from {', '.join(SCENE_NAMES)}")

    aircraft = []
    trails = {}
    for index, flight in enumerate(flights, start=1):
        icao = f"{0x4B0000 + index:06x}"
        lat, lon = destination(*flight.ref, flight.bearing, flight.dist_km)
        aircraft.append(
            Aircraft(
                icao=icao,
                lat=lat,
                lon=lon,
                callsign=flight.callsign,
                type_code=flight.type_code,
                altitude_ft=flight.altitude_ft,
                track_deg=flight.track_deg,
                ground_speed_kt=flight.ground_speed_kt,
                vertical_rate_fpm=flight.vertical_rate_fpm,
                **flight.extra,
            )
        )
        if flight.track_deg is not None and flight.ground_speed_kt:
            trails[icao] = _trail(lat, lon, flight)

    snapshot = TrafficSnapshot(
        aircraft=tuple(aircraft),
        timestamp=timestamp,
        source="synthetic",
        total_reported=len(aircraft),
    )
    return SyntheticScene(name=name, snapshot=snapshot, trails=trails, now=now)


def _trail(lat: float, lon: float, flight: _Flight) -> tuple[tuple[float, float], ...]:
    """Back-project past positions at one-minute intervals, oldest first."""
    assert flight.track_deg is not None and flight.ground_speed_kt is not None
    step_km = flight.ground_speed_kt * KM_PER_NM * TRAIL_INTERVAL_S / 3600
    points = []
    for k in range(flight.trail):
        track = flight.track_deg - flight.turn_deg_per_min * (k + 0.5)
        lat, lon = destination(lat, lon, track + 180.0, step_km)
        points.append((lat, lon))
    return tuple(reversed(points))
