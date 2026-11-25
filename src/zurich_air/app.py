"""The live loop: fetch → history → scene → render → display.

Deliberately simple. Refresh scheduling for the e-paper panel, quiet hours and the
systemd service are Milestone 7.
"""

from __future__ import annotations

import logging
import math
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from zurich_air.config import Config
from zurich_air.data.aircraft import TrafficSnapshot
from zurich_air.data.provider import ProviderError, TrafficProvider
from zurich_air.display.base import Display
from zurich_air.pipeline import build_scene
from zurich_air.processing.history import TrackHistory
from zurich_air.rendering.renderer import Renderer

log = logging.getLogger(__name__)

FETCH_MARGIN_KM = 10.0  # fetch a little beyond the radar so trails exist on entry
TICK_OFFSET_S = 1.0  # tick just after the interval boundary, so the clock has turned
MIN_GAP_S = 2.0  # never tick twice within this, even after a slow update


class App:
    def __init__(
        self,
        config: Config,
        provider: TrafficProvider,
        display: Display,
        *,
        renderer: Renderer | None = None,
        history: TrackHistory | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.display = display
        self.renderer = renderer or Renderer(config)
        self.history = history or TrackHistory()
        self.snapshot: TrafficSnapshot | None = None  # last good data
        self.last_success: datetime | None = None  # local time it arrived
        self.featured: tuple[str, ...] = ()  # ICAO addresses in the flight list, in order
        self._shown: bytes | None = None

    def tick(self, now: datetime | None = None) -> bool:
        """Fetch, render and show one frame; True if fresh data arrived.

        On a provider failure the last good snapshot is kept and the scene turns stale
        once it is older than ``update.stale_after_s``. The display is only updated when
        the frame differs from the one already shown.
        """
        now = now or datetime.now(UTC)
        location = self.config.location
        try:
            snapshot = self.provider.fetch(
                location.latitude, location.longitude, self.config.radar.range_km + FETCH_MARGIN_KM
            )
        except ProviderError as exc:
            kept = (
                f"keeping data from {self.snapshot.timestamp:%H:%M:%S} UTC"
                if self.snapshot
                else "no data yet"
            )
            log.warning("%s; %s", exc, kept)
            fresh = False
        else:
            self.snapshot, self.last_success = snapshot, now
            self.history.update(snapshot)
            fresh = True

        scene = build_scene(
            self.snapshot, self.history, self.config, now, self.last_success, self.featured
        )
        self.featured = tuple(a.icao for a in scene.featured)
        image = self.renderer.render(scene)
        frame = image.tobytes()
        if frame != self._shown:
            self.display.show(image)
            self._shown = frame
        else:
            log.debug("frame unchanged; display not refreshed")
        return fresh

    def run(self, stop: threading.Event | None = None, *, handle_signals: bool = True) -> None:
        """Tick at every ``update.interval_s`` boundary until *stop* is set or SIGINT/SIGTERM."""
        stop = stop or threading.Event()
        interval = self.config.update.interval_s
        log.info("running: update every %.0f s", interval)
        with _stop_on_signals(stop, enabled=handle_signals):
            try:
                while not stop.is_set():
                    try:
                        self.tick()
                    except Exception:  # keep the display alive; the next tick may work
                        log.exception("update failed")
                    stop.wait(seconds_until_next_tick(time.time(), interval))
            finally:
                self.close()
        log.info("stopped")

    def close(self) -> None:
        self.display.close()


def seconds_until_next_tick(now_ts: float, interval_s: float) -> float:
    """Delay until just after the next multiple of *interval_s* (epoch time), so
    updates land on whole minutes; at least MIN_GAP_S so errors never cause a burst."""
    boundary = (math.floor((now_ts - TICK_OFFSET_S) / interval_s) + 1) * interval_s
    delay = boundary + TICK_OFFSET_S - now_ts
    return delay + interval_s if delay < MIN_GAP_S else delay


@contextmanager
def _stop_on_signals(stop: threading.Event, *, enabled: bool) -> Iterator[None]:
    if not enabled:
        yield
        return
    signals = (signal.SIGINT, signal.SIGTERM)

    def handler(signum: int, _frame: object) -> None:
        log.info("received %s, stopping", signal.Signals(signum).name)
        stop.set()

    previous = {s: signal.signal(s, handler) for s in signals}
    try:
        yield
    finally:
        for s, old in previous.items():
            signal.signal(s, old)
