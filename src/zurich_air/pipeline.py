"""Turn a traffic snapshot into the Scene the renderer draws.

This is the one path from data to screen, shared by the live app and the ``preview``
command: filter → rank → trails → live/stale status → attribution.
"""

from __future__ import annotations

from datetime import datetime

from zurich_air.config import Config
from zurich_air.data.aircraft import TrafficSnapshot
from zurich_air.processing.filtering import visible_aircraft
from zurich_air.processing.history import TrackHistory
from zurich_air.processing.ranking import select_featured
from zurich_air.rendering.renderer import Scene


def build_scene(
    snapshot: TrafficSnapshot | None,
    history: TrackHistory | None,
    config: Config,
    now: datetime,
    last_success: datetime | None = None,
) -> Scene:
    """The frame to show at *now* (timezone-aware).

    *snapshot* is the most recent good data, or None if there never was any.
    *last_success* is the local time it was fetched; staleness is measured from it so
    that a provider clock that runs fast or slow cannot flip the status. Without it,
    the snapshot's own timestamp is used (saved and synthetic data).
    """
    if snapshot is None:
        return Scene(aircraft=[], featured=[], updated_at=now, status="stale", now=now)

    aircraft = visible_aircraft(snapshot.aircraft, config)
    trails = history.trails(aircraft) if history is not None and config.radar.trails else {}
    age_s = (now - (last_success or snapshot.timestamp)).total_seconds()
    return Scene(
        aircraft=aircraft,
        featured=select_featured(aircraft, config),
        updated_at=snapshot.timestamp,
        trails=trails,
        status="stale" if age_s > config.update.stale_after_s else "live",
        now=now,
        source_label=snapshot.source.upper() if snapshot.source else None,
    )
