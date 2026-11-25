"""Small-scale geodesy for the radar.

The radar uses an azimuthal equidistant projection centred on the configured location:
every aircraft is placed at its true great-circle distance and initial bearing from the
centre. Within the ~100 km radius of the display this is effectively exact and needs no
GIS dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088  # IUGG mean Earth radius
KM_PER_NM = 1.852
M_PER_FT = 0.3048

_CARDINALS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine) between two WGS84 points, in km."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees clockwise from north."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return math.degrees(math.atan2(x, y)) % 360.0


def destination(lat: float, lon: float, bearing: float, dist_km: float) -> tuple[float, float]:
    """Point reached travelling *dist_km* from (lat, lon) on initial *bearing* degrees."""
    delta = dist_km / EARTH_RADIUS_KM
    theta = math.radians(bearing)
    phi1, lmb1 = math.radians(lat), math.radians(lon)
    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    lmb2 = lmb1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * sin_phi2,
    )
    return math.degrees(phi2), (math.degrees(lmb2) + 540.0) % 360.0 - 180.0


def east_north_km(
    origin_lat: float, origin_lon: float, lat: float, lon: float
) -> tuple[float, float]:
    """Azimuthal-equidistant (east, north) offset of a point from the origin, in km."""
    d = distance_km(origin_lat, origin_lon, lat, lon)
    if d == 0.0:
        return 0.0, 0.0
    b = math.radians(bearing_deg(origin_lat, origin_lon, lat, lon))
    return d * math.sin(b), d * math.cos(b)


def angle_diff(a: float, b: float) -> float:
    """Signed smallest difference a - b between two angles, in (-180, 180]."""
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


def cardinal(bearing: float) -> str:
    """Eight-point compass name for a bearing in degrees."""
    return _CARDINALS[round((bearing % 360.0) / 45.0) % 8]


@dataclass(frozen=True)
class RadarProjection:
    """Maps geographic coordinates onto a circular radar scope in screen pixels.

    Screen convention: x grows to the right (east), y grows downwards (south).
    """

    origin_lat: float
    origin_lon: float
    range_km: float
    center_x: float
    center_y: float
    radius_px: float

    @property
    def px_per_km(self) -> float:
        return self.radius_px / self.range_km

    def offset_to_screen(self, east_km: float, north_km: float) -> tuple[float, float]:
        s = self.px_per_km
        return self.center_x + east_km * s, self.center_y - north_km * s

    def to_screen(self, lat: float, lon: float) -> tuple[float, float]:
        east, north = east_north_km(self.origin_lat, self.origin_lon, lat, lon)
        return self.offset_to_screen(east, north)

    def distance_from_center_km(self, lat: float, lon: float) -> float:
        return distance_km(self.origin_lat, self.origin_lon, lat, lon)

    def in_range(self, lat: float, lon: float) -> bool:
        return self.distance_from_center_km(lat, lon) <= self.range_km
