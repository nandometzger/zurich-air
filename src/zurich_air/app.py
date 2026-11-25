"""The live loop: fetch → history → scene → render → display, built to run for months.

No single failure stops the display. A provider error keeps the last good data (shown
stale, later hidden); an unexpected exception in any stage is logged, with a traceback
at most hourly per kind, and the next update carries on. Only a display that keeps
failing ends the process, with a non-zero status so that systemd restarts it.

Updates follow ``update.interval_s``, and ``update.quiet_interval_s`` during quiet hours.
The log gets one line per update and a summary every hour.

Refresh policies for the e-paper panel are Milestone 6.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from zoneinfo import ZoneInfo

from PIL import Image

from zurich_air import schedule
from zurich_air.config import Config
from zurich_air.data.aircraft import TrafficSnapshot
from zurich_air.data.provider import ProviderError, TrafficProvider
from zurich_air.display.base import Display
from zurich_air.pipeline import build_scene
from zurich_air.processing.history import TrackHistory
from zurich_air.rendering.renderer import Renderer

log = logging.getLogger(__name__)
T = TypeVar("T")

FETCH_MARGIN_KM = 10.0  # fetch a little beyond the radar so trails exist on entry
MAX_DISPLAY_FAILURES = 3  # in a row; then exit non-zero so that systemd restarts us
CLOCK_RETRY_S = 30.0  # how often to look again at a clock that is not set yet
TRACEBACK_EVERY_S = 3600.0  # full traceback of a recurring error at most this often
SUMMARY_EVERY_S = 3600.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TickReport:
    """What one update did; its text is the heartbeat line in the log."""

    fresh: bool = False  # new data arrived
    source: str | None = None  # where it came from
    problem: str | None = None  # why no new data arrived
    status: str = "unknown"  # of the scene: live, stale or waiting
    aircraft: int = 0  # on the radar
    featured: tuple[str, ...] = ()  # flight list labels, as on the display
    refreshed: bool = False  # the display was updated
    errors: tuple[str, ...] = ()  # stages that raised unexpectedly
    clock_wrong: bool = False  # skipped: the system clock is not set yet

    def __str__(self) -> str:
        parts = [(self.source or "?") if self.fresh else "no new data", self.status]
        parts.append(f"{self.aircraft} aircraft")
        if self.featured:
            parts.append("featured " + " ".join(self.featured))
        parts.append("display refreshed" if self.refreshed else "display unchanged")
        line = "update: " + ", ".join(parts)
        if self.problem:
            line += f" ({self.problem})"
        if self.errors:
            line += f"; failed: {', '.join(self.errors)}"
        return line


class ErrorLog:
    """Logs unexpected exceptions without flooding the journal: the full traceback at most
    once per *every_s* for each kind (stage and exception type), a single line otherwise."""

    def __init__(
        self, every_s: float = TRACEBACK_EVERY_S, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.every_s = every_s
        self._clock = clock
        self._last: dict[tuple[str, str], float] = {}

    def report(self, stage: str, exc: Exception) -> None:
        kind = (stage, type(exc).__name__)
        now = self._clock()
        last = self._last.get(kind)
        if last is None or now - last >= self.every_s:
            self._last[kind] = now
            log.error("%s failed: %s", stage, _describe(exc), exc_info=exc)
        else:
            log.error("%s failed again: %s (traceback logged earlier)", stage, _describe(exc))


class HourlySummary:
    """Counts what the updates did and sums it up once per *every_s*."""

    def __init__(
        self, every_s: float = SUMMARY_EVERY_S, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.every_s = every_s
        self._clock = clock
        self._since = clock()
        self._counts: Counter[str] = Counter()
        self._failed_before: Mapping[str, int] = {}

    def add(self, report: TickReport, source_failures: Mapping[str, int]) -> str | None:
        """Count *report*; when the period is over, return the summary and start afresh.

        *source_failures* are the failed requests per source since start.
        """
        counts = self._counts
        if report.clock_wrong:
            counts["skipped (clock not set)"] += 1
        else:
            counts["updates"] += 1
            counts["with new data"] += report.fresh
            counts["display refreshes"] += report.refreshed
            counts["errors"] += len(report.errors)
        now = self._clock()
        if now - self._since < self.every_s:
            return None
        keys = ["updates", "with new data", "display refreshes", "errors"]
        keys += [k for k in counts if k not in keys]
        line = "last hour: " + ", ".join(f"{counts[k]} {k}" for k in keys)
        if source_failures:
            failed = (
                f"{s} {n - self._failed_before.get(s, 0)}" for s, n in source_failures.items()
            )
            line += "; failed requests: " + ", ".join(failed)
        self._since, self._counts, self._failed_before = now, Counter(), dict(source_failures)
        return line


class App:
    def __init__(
        self,
        config: Config,
        provider: TrafficProvider,
        display: Display,
        *,
        renderer: Renderer | None = None,
        history: TrackHistory | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self.provider = provider
        self.display = display
        self.renderer = renderer or Renderer(config)
        self.history = history or TrackHistory()
        self.clock = clock
        self.snapshot: TrafficSnapshot | None = None  # last good data
        self.last_success: datetime | None = None  # local time it arrived
        self.featured: tuple[str, ...] = ()  # ICAO addresses in the flight list, in order
        self.display_failures = 0  # in a row
        self.errors = ErrorLog()
        self.summary = HourlySummary()
        self._shown: bytes | None = None
        self._tz = ZoneInfo(config.location.timezone)
        self._quiet = config.update.quiet
        self._in_quiet = False
        self._clock_wrong = False

    def tick(self, now: datetime | None = None) -> TickReport:
        """Fetch, render and show one frame, and log what happened. Never raises.

        On a provider failure the last good snapshot is kept; the scene turns stale and
        later empty as it ages (see pipeline.py). The display is only updated when the
        frame differs from the one already shown. Nothing happens while the system
        clock is not set.
        """
        now = now or self.clock()
        if not self._clock_is_set(now):
            return TickReport(clock_wrong=True)
        errors: list[str] = []
        problem = self._fetch(now, errors)
        scene = self._attempt(
            "build",
            errors,
            lambda: build_scene(
                self.snapshot, self.history, self.config, now, self.last_success, self.featured
            ),
        )
        image = None
        if scene is not None:
            self.featured = tuple(a.icao for a in scene.featured)
            image = self._attempt("render", errors, lambda: self.renderer.render(scene))
        refreshed = image is not None and self._show(image, errors)
        fresh = problem is None
        report = TickReport(
            fresh=fresh,
            source=self.snapshot.source if fresh and self.snapshot else None,
            problem=problem,
            status=scene.status if scene else "unknown",
            aircraft=len(scene.aircraft) if scene else 0,
            featured=tuple(a.label for a in scene.featured) if scene else (),
            refreshed=refreshed,
            errors=tuple(errors),
        )
        log.log(logging.INFO if fresh and not errors else logging.WARNING, "%s", report)
        return report

    def run(self, stop: threading.Event | None = None, *, handle_signals: bool = True) -> int:
        """Update on every interval until *stop* is set or SIGINT/SIGTERM arrives.

        Returns the exit status: 0 when asked to stop, 1 when the display has failed
        MAX_DISPLAY_FAILURES times in a row (systemd then restarts the service).
        """
        stop = stop or threading.Event()
        update = self.config.update
        quiet = ""
        if self._quiet:
            quiet = f" ({update.quiet_interval_s:.0f} s in quiet hours {self._quiet})"
        log.info("running: update every %.0f s%s", update.interval_s, quiet)
        with _stop_on_signals(stop, enabled=handle_signals):
            try:
                status = self._loop(stop)
            finally:
                self.close()
        log.info("stopped")
        return status

    def _loop(self, stop: threading.Event) -> int:
        while not stop.is_set():
            try:
                report = self.tick()
            except Exception as exc:  # tick guards each stage; this is the last resort
                self.errors.report("update", exc)
                report = TickReport(errors=("update",))
            if summary := self.summary.add(report, _source_failures(self.provider)):
                log.info("%s", summary)
            if self.display_failures >= MAX_DISPLAY_FAILURES:
                log.error(
                    "the display failed %d times in a row; exiting so that systemd restarts us",
                    self.display_failures,
                )
                return 1
            delay = CLOCK_RETRY_S if report.clock_wrong else self.next_delay(self.clock())
            stop.wait(delay)
        return 0

    def next_delay(self, now: datetime) -> float:
        """Seconds from *now* until the next update; logs when quiet hours begin or end."""
        update = self.config.update
        local = now.astimezone(self._tz)
        quiet = self._quiet if self._quiet and self._quiet.contains(local) else None
        if (quiet is not None) != self._in_quiet:
            self._in_quiet = quiet is not None
            if quiet:
                until = f"{quiet.end:%H:%M}"
                log.info(
                    "quiet hours until %s: updating every %.0f s", until, update.quiet_interval_s
                )
            else:
                log.info("quiet hours over: updating every %.0f s", update.interval_s)
        return schedule.next_delay(local, update.interval_s, self._quiet, update.quiet_interval_s)

    def close(self) -> None:
        try:
            self.display.close()
        except Exception:
            log.exception("closing the display failed")

    def _clock_is_set(self, now: datetime) -> bool:
        if schedule.clock_is_plausible(now):
            if self._clock_wrong:
                log.info("system clock is set now")
                self._clock_wrong = False
            return True
        if not self._clock_wrong:
            log.warning(
                "system clock reads %s, before %s: not updating until it is set "
                "(checking every %.0f s)",
                f"{now:%Y-%m-%d %H:%M}",
                f"{schedule.EARLIEST_PLAUSIBLE:%Y-%m-%d}",
                CLOCK_RETRY_S,
            )
            self._clock_wrong = True
        return False

    def _fetch(self, now: datetime, errors: list[str]) -> str | None:
        """Ask the provider for traffic; None on success, else why no new data arrived."""
        location = self.config.location
        try:
            snapshot = self.provider.fetch(
                location.latitude, location.longitude, self.config.radar.range_km + FETCH_MARGIN_KM
            )
            self.history.update(snapshot)
        except ProviderError as exc:
            return str(exc)
        except Exception as exc:
            self.errors.report("fetch", exc)
            errors.append("fetch")
            return "unexpected error"
        self.snapshot, self.last_success = snapshot, now
        return None

    def _attempt(self, stage: str, errors: list[str], fn: Callable[[], T]) -> T | None:
        """``fn()``, or None after logging an unexpected exception."""
        try:
            return fn()
        except Exception as exc:
            self.errors.report(stage, exc)
            errors.append(stage)
            return None

    def _show(self, image: Image.Image, errors: list[str]) -> bool:
        """Put *image* on the display unless it is there already; True if it was."""
        frame = image.tobytes()
        if frame == self._shown:
            log.debug("frame unchanged; display not refreshed")
            return False
        try:
            self.display.show(image)
        except Exception as exc:
            self.display_failures += 1
            self.errors.report("display", exc)
            errors.append("display")
            return False
        self._shown = frame
        self.display_failures = 0
        return True


def _source_failures(provider: TrafficProvider) -> Mapping[str, int]:
    """Failed requests per source since start, if the provider counts them."""
    counts = getattr(provider, "failure_counts", None)  # FallbackProvider does
    return counts() if callable(counts) else {}


def _describe(exc: BaseException, limit: int = 200) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[: limit - 3] + "..."


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
