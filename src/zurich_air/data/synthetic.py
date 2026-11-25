"""Deterministic synthetic traffic scenes for visual development and tests.

Nothing here is ever used by the live data path — this module is imported only by the
``preview`` command and the test suite.

The scenes are modelled on real Zürich operations on an afternoon with the usual westerly
concept: arrivals on the ILS 14 from the north-west, departures off runways 16 and 28,
overflights spread across the sky, a Rega helicopter. Every flight is invented, but looks
like the real thing: callsigns are real airline ICAO prefixes with the numeric or
alphanumeric flight numbers airlines use on the radio (SWR8, EDW24, DLH5LU, EZS14QA, …),
and registrations follow an ``HB-?X?`` pattern.
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
    # Arrivals: a queue on the ILS 14, one turning onto final, feeders from west and east.
    _final("UAE87", "A388", 8, 150),
    _final("SWR93D", "BCS3", 21, 180),
    _Flight("DLH5LU", "CRJ9", THR_14, 345, 31, 7_000, 165, 220, -700, turn_deg_per_min=-12),
    _Flight("BAW8KN", "A320", AIRPORT, 272, 41, 10_000, 84, 280, -1500),
    _Flight("AUA57T", "E195", AIRPORT, 76, 37, 11_000, 262, 290, -1400),
    # Departures: runway 16 to the south, runway 28 to the west.
    _Flight("EDW24", "A359", END_16, 178, 12, 7_100, 214, 230, 2400, turn_deg_per_min=14),
    _Flight("SWR8", "B77W", END_28, 271, 9, 5_400, 268, 210, 2200),
    _Flight("OAW726", "E295", END_16, 188, 30, 12_000, 238, 290, 1900, turn_deg_per_min=6),
    # Overflights, spread out at cruising levels.
    _Flight("THY7HJ", "A21N", CITY, 302, 51, 37_000, 102, 470),
    _Flight("RYR4TR", "B38M", CITY, 38, 46, 39_000, 226, 455),
    _Flight("WZZ1UA", "A21N", CITY, 214, 54, 35_000, 64, 480, 64),
    _Flight("AFR56GW", "A20N", CITY, 160, 37, 34_000, 25, 440),
    _Flight("QTR4CY", "B789", CITY, 345, 57, 40_000, 133, 505),
    _Flight("EZS14QA", "A20N", CITY, 118, 49, 24_000, 296, 410, 1200),
    _Flight("ETD19", "B789", CITY, 248, 33, 38_000, 121, 495),
    # Rega on a mission and a light aircraft out of the city.
    _Flight("SAZ3", "EC45", CITY, 140, 10, 2_600, 204, 125, -200, trail=4),
    _Flight(
        "HBCXB", "C172", CITY, 232, 25, 3_500, 236, 95, 0, trail=4,
        extra={"registration": "HB-CXB"},
    ),
)  # fmt: skip

_QUIET = (
    _final("SWR1325", "A20N", 14, 160),
    _Flight("THY7HJ", "A21N", CITY, 302, 44, 37_000, 102, 470),
    _Flight("SAZ3", "EC45", CITY, 140, 10, 2_600, 204, 125, -200, trail=4),
)

# Busy: typical traffic plus a longer arrival queue, more departures and overflights.
_BUSY_EXTRA = (
    _final("LOT41K", "E195", 30, 185),
    _final("CLH2CA", "CRJ9", 39, 205),
    _Flight("SWR11MH", "A21N", THR_14, 10, 26, 8_000, 205, 230, -900, turn_deg_per_min=-8),
    _Flight("DLA5TP", "E190", AIRPORT, 28, 44, 12_000, 210, 300, -1600),
    _Flight("AEE3SG", "A20N", AIRPORT, 98, 47, 13_000, 270, 320, -1400),
    _Flight("SWR18L", "A320", END_16, 168, 21, 9_500, 172, 260, 2300),
    _Flight("EDW71", "A320", END_28, 276, 22, 9_000, 284, 270, 2000, turn_deg_per_min=8),
    _Flight("GEC8197", "B77L", END_28, 300, 41, 17_000, 318, 360, 1500),
)
_BUSY_AIRLINES = (
    ("BAW", "A320"), ("KLM", "B738"), ("DLH", "A20N"), ("AFR", "A20N"), ("IBE", "A321"),
    ("VLG", "A320"), ("SAS", "A20N"), ("FIN", "A321"), ("UAL", "B763"), ("DAL", "A339"),
    ("ACA", "B788"), ("SIA", "A359"), ("PGT", "B38M"), ("TAP", "A21N"),
)  # fmt: skip


def _busy_overflights() -> tuple[_Flight, ...]:
    rng = random.Random(2412)
    flights = []
    for prefix, type_code in _BUSY_AIRLINES:
        if rng.random() < 0.5:  # a plain flight number or a number with letters
            number = str(rng.randrange(1, 2000))
        else:
            number = str(rng.randrange(1, 100)) + "".join(rng.sample("ABCDEFGHJKLMNPRSTUVWXYZ", 2))
        flights.append(
            _Flight(
                prefix + number,
                type_code,
                CITY,
                rng.uniform(0, 360),
                rng.uniform(12, 58),
                rng.randrange(300, 410, 10) * 100,
                round(rng.uniform(0, 360)),
                rng.uniform(420, 510),
                rng.choice((0, 0, 64, -64)),
            )
        )
    return tuple(flights)


# Degraded: incomplete and odd data, as real ADS-B feeds produce it.
_DEGRADED = (
    _final("SWR1325", "A20N", 12, 160),
    _Flight(None, None, CITY, 20, 18, 4_200, 300, 180, 900, extra={"registration": "HB-JXC"}),
    _Flight(None, None, AIRPORT, 100, 9, None, None, None, None),  # position only
    _Flight("SWR38", "B77W", AIRPORT, 300, 26, None, 92, 250, None),  # no altitude
    _Flight("XAX5", "ZZZZ", CITY, 330, 45, 51_000, 140, 520, 6000),  # unknown codes
    _Flight("EZS90ZZ", "A20N", CITY, 160, 40, -50, 359.9, 30, -5000),  # odd values
    _Flight("DLH7AB", "A320", AIRPORT, 120, 1, 1_400, 280, 15, 0, extra={"on_ground": True}),
    _Flight(
        "SWR2RJ", "A320", CITY, 200, 22, 9_000, 211, 260, -2500,
        extra={"squawk": "7700", "emergency": True},
    ),
    _Flight("  ", "A21N", CITY, 60, 50, 24_000, 725.0, 400, 0, extra={"registration": ""}),
    _Flight("ABCDEFGHIJK", "LUFTHANSA-LONG", AIRPORT, 250, 12, 3_800, 45, 180, 0),  # too long
    _Flight("SUI10", "F18", CITY, 280, 35, 18_000, 180, 380, 0, extra={"military": True}),
    _Flight("EZY52TC", None, CITY, 15, 52, 33_000, None, 440, 0, extra={"position_age_s": 45.0}),
    _Flight("WZZ8PU", "A21N", CITY, 95, 59.9, 36_000, 270, 450, 0),  # at the edge of range
)  # fmt: skip


def scene(name: str) -> SyntheticScene:
    """Build the synthetic scene *name* (one of :data:`SCENE_NAMES`)."""
    now = TIMESTAMP
    timestamp = TIMESTAMP
    if name == "typical":
        flights = _TYPICAL
    elif name == "busy":
        flights = _TYPICAL + _BUSY_EXTRA + _busy_overflights()
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
        source="synthetic data",  # shown as the attribution in the footer
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
