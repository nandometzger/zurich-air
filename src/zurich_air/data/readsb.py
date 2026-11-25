"""Parse readsb-style JSON (adsb.fi, adsb.lol, a local readsb) into a TrafficSnapshot.

Community ADS-B aggregators publish the JSON format of the readsb decoder::

    {"now": 1795639820001, "total": 31, "ac": [{"hex": "4b1805", "flight": "SWR57Z  ",
      "t": "BCS3", "alt_baro": 6225, "gs": 183.7, "track": 59.95, "lat": 47.5, ...}, ...]}

Any field can be missing and values are occasionally of the wrong type, so every field is
coerced defensively: a bad value becomes ``None`` and never raises. Only entries without
an address or a valid position are dropped, because they cannot be placed on the radar.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from zurich_air.data.aircraft import Aircraft, TrafficSnapshot

EMERGENCY_SQUAWKS = frozenset({"7500", "7600", "7700"})  # hijack, radio failure, emergency
# Bits of the aircraft-database flags ("dbFlags") published by adsb.fi, adsb.lol and readsb.
DB_FLAG_MILITARY = 1
DB_FLAG_INTERESTING = 2
DB_FLAG_PIA = 4  # Privacy ICAO Address: a temporary address that hides the owner
DB_FLAG_LADD = 8  # Limiting Aircraft Data Displayed: the owner opted out of public tracking
_HEX = re.compile(r"~?[0-9a-f]{6}")
_MS_EPOCH_THRESHOLD = 1e11  # "now" above this is in milliseconds (API), else seconds (readsb)


class ReadsbError(ValueError):
    """The payload is not a readsb aircraft response at all."""


def parse_snapshot(payload: Any, *, source: str, fetched_at: datetime) -> TrafficSnapshot:
    """Normalise a decoded readsb JSON *payload* into a snapshot.

    *fetched_at* (timezone-aware) is used as the snapshot time when the payload has no
    usable ``now`` field.
    """
    if not isinstance(payload, Mapping):
        raise ReadsbError(f"expected a JSON object, got {type(payload).__name__}")
    entries = payload.get("ac", payload.get("aircraft"))
    if not isinstance(entries, list):
        raise ReadsbError("response has no aircraft list")
    aircraft = tuple(a for a in map(parse_aircraft, entries) if a is not None)
    total = _int(payload.get("total"))
    return TrafficSnapshot(
        aircraft=aircraft,
        timestamp=_timestamp(payload.get("now")) or fetched_at,
        source=source,
        total_reported=total if total is not None and total >= 0 else len(entries),
    )


def parse_aircraft(entry: Any) -> Aircraft | None:
    """One readsb aircraft entry, or ``None`` if it has no address or position."""
    if not isinstance(entry, Mapping):
        return None
    icao = _str(entry.get("hex"))
    if icao is None or not _HEX.fullmatch(icao := icao.lower()):
        return None
    lat, lon = _float(entry.get("lat")), _float(entry.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    alt_baro = entry.get("alt_baro")
    on_ground = isinstance(alt_baro, str) and alt_baro.strip().lower() == "ground"
    track = _first(_float(entry.get("track")), _float(entry.get("true_heading")))
    squawk = _squawk(entry.get("squawk"))
    emergency_field = _str(entry.get("emergency"))
    db_flags = _int(entry.get("dbFlags"))
    return Aircraft(
        icao=icao,
        lat=lat,
        lon=lon,
        callsign=_str(entry.get("flight")),
        registration=_str(entry.get("r")),
        type_code=_upper(entry.get("t")),
        altitude_ft=None if on_ground else _int(alt_baro),
        on_ground=on_ground,
        track_deg=None if track is None else track % 360.0,
        ground_speed_kt=_non_negative(_float(entry.get("gs"))),
        vertical_rate_fpm=_first(_int(entry.get("baro_rate")), _int(entry.get("geom_rate"))),
        category=_upper(entry.get("category")),
        squawk=squawk,
        emergency=(emergency_field is not None and emergency_field.lower() != "none")
        or squawk in EMERGENCY_SQUAWKS,
        military=_flag(db_flags, DB_FLAG_MILITARY),
        interesting=_flag(db_flags, DB_FLAG_INTERESTING),
        privacy=_flag(db_flags, DB_FLAG_PIA | DB_FLAG_LADD),
        position_age_s=_first(
            _non_negative(_float(entry.get("seen_pos"))),
            _non_negative(_float(entry.get("seen"))),
            0.0,
        ),
    )


# --- defensive coercion -------------------------------------------------------------------


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _float(value: Any) -> float | None:
    """A finite number; bools, strings and NaN/inf become None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _int(value: Any) -> int | None:
    number = _float(value)
    return None if number is None else round(number)


def _flag(flags: int | None, mask: int) -> bool:
    return flags is not None and flags >= 0 and bool(flags & mask)


def _non_negative(value: float | None) -> float | None:
    return value if value is not None and value >= 0 else None


def _str(value: Any) -> str | None:
    """A stripped, printable string; blank or non-string values become None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value and value.isprintable() else None


def _upper(value: Any) -> str | None:
    text = _str(value)
    return None if text is None else text.upper()


def _squawk(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 7777:
        value = f"{value:04d}"
    text = _str(value)
    return text if text is not None and len(text) == 4 and text.isdigit() else None


def _timestamp(value: Any) -> datetime | None:
    seconds = _float(value)
    if seconds is None or seconds <= 0:
        return None
    if seconds > _MS_EPOCH_THRESHOLD:
        seconds /= 1000.0
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None
