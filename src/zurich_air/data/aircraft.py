"""Provider-independent aircraft data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Aircraft:
    """One aircraft observation, normalised across data providers.

    Only the ICAO address and position are required; everything else is optional because
    real ADS-B data is frequently incomplete. Units follow aviation convention:
    feet, knots and feet per minute.
    """

    icao: str  # 24-bit ICAO address, lowercase hex
    lat: float
    lon: float
    callsign: str | None = None
    registration: str | None = None
    type_code: str | None = None  # ICAO type designator, e.g. "A20N"
    altitude_ft: int | None = None  # barometric altitude
    on_ground: bool = False
    track_deg: float | None = None  # true track over ground
    ground_speed_kt: float | None = None
    vertical_rate_fpm: int | None = None
    category: str | None = None  # ADS-B emitter category, e.g. "A3"
    squawk: str | None = None
    emergency: bool = False
    military: bool = False
    interesting: bool = False  # flagged as interesting in the provider's aircraft database
    privacy: bool = False  # owner opted out of public tracking (LADD) or uses a privacy address
    position_age_s: float = 0.0  # seconds since the position was last updated

    @property
    def label(self) -> str:
        """Best human-readable identifier: callsign, registration or ICAO address."""
        for value in (self.callsign, self.registration):
            if value and value.strip():
                return value.strip()
        return self.icao.upper()


@dataclass(frozen=True)
class TrafficSnapshot:
    """All aircraft reported by a provider at one point in time."""

    aircraft: tuple[Aircraft, ...]
    timestamp: datetime  # timezone-aware, time the data refers to
    source: str
    total_reported: int = 0
    metadata: dict[str, str] = field(default_factory=dict)
