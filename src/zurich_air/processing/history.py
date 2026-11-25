"""Recent positions of each aircraft, for the dotted trails on the radar.

Providers only report where an aircraft is now, so the trail is built up from successive
snapshots. Memory stays bounded: each aircraft keeps at most ``max_points`` positions no
older than ``max_age_s``, aircraft not reported for ``forget_after_s`` are dropped, and
at most ``max_aircraft`` are tracked at once.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from zurich_air.data.aircraft import Aircraft, TrafficSnapshot

Point = tuple[float, float]  # (lat, lon)


@dataclass
class _Track:
    points: deque[tuple[datetime, float, float]]  # (time, lat, lon), oldest first
    last_seen: datetime


class TrackHistory:
    def __init__(
        self,
        max_points: int = 8,
        max_age_s: float = 600.0,
        forget_after_s: float = 300.0,
        max_aircraft: int = 2000,
    ) -> None:
        if max_points < 1 or max_aircraft < 1:
            raise ValueError("max_points and max_aircraft must be positive")
        self.max_points = max_points
        self.max_age = timedelta(seconds=max_age_s)
        self.forget_after = timedelta(seconds=forget_after_s)
        self.max_aircraft = max_aircraft
        self._tracks: dict[str, _Track] = {}

    def __len__(self) -> int:
        return len(self._tracks)

    def __contains__(self, icao: object) -> bool:
        return icao in self._tracks

    def add(self, icao: str, lat: float, lon: float, at: datetime) -> None:
        """Record that *icao* was at (lat, lon) at time *at* (timezone-aware)."""
        track = self._tracks.get(icao)
        if track is None:
            track = self._tracks[icao] = _Track(deque(maxlen=self.max_points), at)
        track.last_seen = max(track.last_seen, at)
        if track.points:
            last_at, last_lat, last_lon = track.points[-1]
            if at <= last_at or (lat, lon) == (last_lat, last_lon):
                return  # repeated or out-of-order report: nothing new
        track.points.append((at, lat, lon))

    def update(self, snapshot: TrafficSnapshot) -> None:
        """Add every aircraft position of *snapshot*, then prune old data."""
        for a in snapshot.aircraft:
            at = snapshot.timestamp - timedelta(seconds=a.position_age_s)
            self.add(a.icao, a.lat, a.lon, at)
        self.prune(snapshot.timestamp)

    def prune(self, now: datetime) -> None:
        """Forget aircraft not seen recently and positions older than ``max_age_s``."""
        for icao in list(self._tracks):
            track = self._tracks[icao]
            if now - track.last_seen > self.forget_after:
                del self._tracks[icao]
                continue
            while track.points and now - track.points[0][0] > self.max_age:
                track.points.popleft()
            if not track.points:
                del self._tracks[icao]
        if len(self._tracks) > self.max_aircraft:
            by_age = sorted(self._tracks, key=lambda icao: self._tracks[icao].last_seen)
            for icao in by_age[: len(self._tracks) - self.max_aircraft]:
                del self._tracks[icao]

    def trail(self, aircraft: Aircraft) -> tuple[Point, ...]:
        """Earlier positions of *aircraft*, oldest first, excluding where it is now."""
        track = self._tracks.get(aircraft.icao)
        if track is None:
            return ()
        points = [(lat, lon) for _, lat, lon in track.points]
        while points and points[-1] == (aircraft.lat, aircraft.lon):
            points.pop()
        return tuple(points)

    def trails(self, aircraft: Iterable[Aircraft]) -> dict[str, tuple[Point, ...]]:
        """Trails of all *aircraft* that have one."""
        result = {}
        for a in aircraft:
            trail = self.trail(a)
            if trail:
                result[a.icao] = trail
        return result
