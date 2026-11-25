"""Typed configuration loaded from a TOML file.

Every setting has a sensible default, so an empty (or missing) config file yields a
working Zürich setup. Unknown keys are rejected to catch typos early.
"""

from __future__ import annotations

import dataclasses
import tomllib
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Zürich Airport (LSZH) runway thresholds as (lat1, lon1, lat2, lon2).
# Source: OurAirports (public domain), https://ourairports.com/
ZRH_RUNWAYS: tuple[tuple[float, float, float, float], ...] = (
    (47.45890, 8.53747, 47.45660, 8.57045),  # 10/28
    (47.48310, 8.53473, 47.46130, 8.56446),  # 14/32
    (47.47560, 8.53595, 47.44540, 8.55673),  # 16/34
)


# Traffic data sources that ``provider.sources`` may name (see data/provider.py).
SOURCE_NAMES: tuple[str, ...] = ("adsb_fi", "adsb_lol")


class ConfigError(ValueError):
    """Raised for invalid configuration files."""


@dataclass(frozen=True)
class LocationConfig:
    name: str = "Zurich"
    latitude: float = 47.3769
    longitude: float = 8.5417
    timezone: str = "Europe/Zurich"


@dataclass(frozen=True)
class AirportConfig:
    code: str = "ZRH"
    latitude: float = 47.458056
    longitude: float = 8.548056
    elevation_ft: int = 1416
    runways: tuple[tuple[float, float, float, float], ...] = ZRH_RUNWAYS


@dataclass(frozen=True)
class RadarConfig:
    range_km: float = 60.0
    ring_step_km: float = 20.0
    trails: bool = True


@dataclass(frozen=True)
class DisplayConfig:
    backend: str = "mock"  # "mock" (PNG files) or "waveshare"
    width: int = 400
    height: int = 300
    rotation: int = 0  # degrees, applied by hardware backends only
    output_dir: str = "previews"
    altitude_units: str = "aviation"  # "aviation" (FL / ft) or "metric" (m)


@dataclass(frozen=True)
class UpdateConfig:
    interval_s: float = 60.0
    stale_after_s: float = 300.0  # show "no data" once the last good fetch is this old
    hide_after_s: float = 1800.0  # then clear radar and list once it is this old


@dataclass(frozen=True)
class AircraftConfig:
    featured: int = 5
    max_altitude_ft: int = 60_000
    include_ground: bool = False
    stale_after_s: float = 60.0  # drop aircraft whose last position is older than this


@dataclass(frozen=True)
class RankingWeights:
    """Points each ranking component adds to an aircraft's score (see processing/ranking.py)."""

    arrival: float = 40.0  # full at the airport, half at ranking.phase_radius_km
    departure: float = 40.0
    notable_type: float = 30.0  # very large or rare: A380, 747, An-124, Beluga, C-17, …
    widebody: float = 10.0
    helicopter: float = 15.0
    rescue: float = 15.0  # air rescue callsign (Rega, SAZ…)
    military: float = 25.0
    interesting: float = 15.0  # flagged as interesting in the aircraft database
    low_altitude: float = 20.0  # full on the ground, nothing from 40 000 ft
    near_centre: float = 10.0  # full at the radar centre, nothing at its edge
    no_identity: float = -20.0  # neither callsign nor type: an uninformative row
    featured_before: float = 15.0  # stickiness: a newcomer must beat this margin


@dataclass(frozen=True)
class RankingConfig:
    phase_radius_km: float = 40.0  # arrivals and departures are recognised this close
    arrival_max_altitude_ft: int = 10_000
    departure_max_altitude_ft: int = 15_000
    min_vertical_rate_fpm: int = 300  # slower climbs and descents count as level flight
    max_track_offset_deg: float = 75.0  # track vs. the direct line to / from the airport
    weights: RankingWeights = field(default_factory=RankingWeights)


@dataclass(frozen=True)
class ProviderConfig:
    sources: tuple[str, ...] = SOURCE_NAMES  # tried in this order
    timeout_s: float = 10.0


@dataclass(frozen=True)
class Config:
    location: LocationConfig = field(default_factory=LocationConfig)
    airport: AirportConfig = field(default_factory=AirportConfig)
    radar: RadarConfig = field(default_factory=RadarConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    aircraft: AircraftConfig = field(default_factory=AircraftConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)
    provider: ProviderConfig = field(default_factory=ProviderConfig)

    def __post_init__(self) -> None:
        _validate(self)


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from *path*; return defaults if *path* is None."""
    if path is None:
        return Config()
    path = Path(path)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    return _build(Config, data, "")


def _build(cls: type, data: dict[str, Any], prefix: str) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"[{prefix.rstrip('.')}] must be a table")
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown config key(s): {', '.join(prefix + k for k in unknown)}")

    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        hint = hints[name]
        if dataclasses.is_dataclass(hint):
            kwargs[name] = _build(hint, value, f"{prefix}{name}.")
        else:
            kwargs[name] = _coerce(value, hint, prefix + name)
    return cls(**kwargs)


def _coerce(value: Any, hint: Any, key: str) -> Any:
    origin = typing.get_origin(hint)
    if origin is tuple:
        if not isinstance(value, list):
            raise ConfigError(f"{key} must be an array")
        inner = typing.get_args(hint)[0]
        return tuple(_coerce(v, inner, key) for v in value)
    if hint is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, hint) or (hint is not bool and isinstance(value, bool)):
        raise ConfigError(f"{key} must be of type {hint.__name__}, got {value!r}")
    return value


def _validate(cfg: Config) -> None:
    if not -90 <= cfg.location.latitude <= 90 or not -180 <= cfg.location.longitude <= 180:
        raise ConfigError("location latitude/longitude out of range")
    if cfg.radar.range_km <= 0 or cfg.radar.ring_step_km <= 0:
        raise ConfigError("radar.range_km and radar.ring_step_km must be positive")
    if cfg.display.backend not in {"mock", "waveshare"}:
        raise ConfigError("display.backend must be 'mock' or 'waveshare'")
    if cfg.display.rotation not in {0, 90, 180, 270}:
        raise ConfigError("display.rotation must be one of 0, 90, 180, 270")
    if cfg.display.altitude_units not in {"aviation", "metric"}:
        raise ConfigError("display.altitude_units must be 'aviation' or 'metric'")
    if not 0 <= cfg.aircraft.featured <= 6:
        raise ConfigError("aircraft.featured must be between 0 and 6")
    if cfg.update.interval_s < 10:
        raise ConfigError("update.interval_s must be at least 10 seconds")
    if cfg.update.stale_after_s <= 0 or cfg.aircraft.stale_after_s <= 0:
        raise ConfigError("update.stale_after_s and aircraft.stale_after_s must be positive")
    if cfg.update.hide_after_s < cfg.update.stale_after_s:
        raise ConfigError("update.hide_after_s must not be less than update.stale_after_s")
    sources = cfg.provider.sources
    if not sources:
        raise ConfigError("provider.sources must name at least one source")
    unknown = [s for s in sources if s not in SOURCE_NAMES]
    if unknown:
        raise ConfigError(
            f"unknown provider source(s): {', '.join(unknown)}; "
            f"choose from {', '.join(SOURCE_NAMES)}"
        )
    if len(set(sources)) != len(sources):
        raise ConfigError("provider.sources must not repeat a source")
    if cfg.provider.timeout_s <= 0:
        raise ConfigError("provider.timeout_s must be positive")
    for runway in cfg.airport.runways:
        if len(runway) != 4:
            raise ConfigError("airport.runways entries must be [lat1, lon1, lat2, lon2]")
    _validate_ranking(cfg.ranking)


MAX_WEIGHT = 100.0  # keeps every weight well below the emergency bonus (ranking.py)


def _validate_ranking(ranking: RankingConfig) -> None:
    if ranking.phase_radius_km <= 0:
        raise ConfigError("ranking.phase_radius_km must be positive")
    if ranking.arrival_max_altitude_ft <= 0 or ranking.departure_max_altitude_ft <= 0:
        raise ConfigError("ranking.*_max_altitude_ft must be positive")
    if ranking.min_vertical_rate_fpm <= 0:
        raise ConfigError("ranking.min_vertical_rate_fpm must be positive")
    if not 0 < ranking.max_track_offset_deg <= 90:
        raise ConfigError("ranking.max_track_offset_deg must be between 0 and 90")
    for f in dataclasses.fields(ranking.weights):
        if not -MAX_WEIGHT <= getattr(ranking.weights, f.name) <= MAX_WEIGHT:
            raise ConfigError(f"ranking.weights.{f.name} must be between -100 and 100")
    if ranking.weights.featured_before < 0:
        raise ConfigError("ranking.weights.featured_before must not be negative")
