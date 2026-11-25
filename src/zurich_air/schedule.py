"""When to update: on whole intervals, less often during quiet hours, never on a bogus clock.

Times are compared as local wall-clock times (``location.timezone``); durations are
computed from timestamps, so a daylight-saving change cannot stretch or shrink a wait.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

TICK_OFFSET_S = 1.0  # tick just after the interval boundary, so the clock has turned
MIN_GAP_S = 2.0  # never tick twice within this, even after a slow update
# A Pi without a real-time clock boots with a clock from the past until NTP sets it.
# Earlier readings than this are certainly wrong: no fetching (TLS would fail) and no
# clock on the display.
EARLIEST_PLAUSIBLE = datetime(2026, 1, 1, tzinfo=UTC)

_WINDOW = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def clock_is_plausible(now: datetime) -> bool:
    return now >= EARLIEST_PLAUSIBLE


def seconds_until_next_tick(now_ts: float, interval_s: float) -> float:
    """Delay until just after the next multiple of *interval_s* (epoch time), so
    updates land on whole minutes; at least MIN_GAP_S so errors never cause a burst."""
    boundary = (math.floor((now_ts - TICK_OFFSET_S) / interval_s) + 1) * interval_s
    delay = boundary + TICK_OFFSET_S - now_ts
    return delay + interval_s if delay < MIN_GAP_S else delay


@dataclass(frozen=True)
class QuietHours:
    """A daily window of local time, ``start`` inclusive to ``end`` exclusive; it crosses
    midnight when ``end`` is earlier than ``start``."""

    start: time
    end: time

    @classmethod
    def parse(cls, text: str) -> QuietHours | None:
        """``"23:30-06:00"`` → a window; ``""`` → None. Raises ValueError otherwise."""
        if not text.strip():
            return None
        match = _WINDOW.fullmatch(text.strip())
        if not match:
            raise ValueError(f'expected "HH:MM-HH:MM" or "", got {text!r}')
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        start, end = time(h1, m1), time(h2, m2)  # ValueError for 24:00, 12:60, …
        if start == end:
            raise ValueError(f"start and end are the same: {text!r}")
        return cls(start, end)

    def __str__(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"

    def contains(self, moment: datetime) -> bool:
        """Whether *moment* (local time) falls inside the window."""
        t = moment.time()
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end

    def seconds_left(self, moment: datetime) -> float:
        """Seconds from *moment* (timezone-aware, local) until the window ends; 0 outside."""
        if not self.contains(moment):
            return 0.0
        end = datetime.combine(moment.date(), self.end, moment.tzinfo)
        if self.end <= moment.time():  # the window ends tomorrow
            end = datetime.combine(moment.date() + timedelta(days=1), self.end, moment.tzinfo)
        return end.timestamp() - moment.timestamp()


def next_delay(
    now: datetime, interval_s: float, quiet: QuietHours | None, quiet_interval_s: float
) -> float:
    """Seconds until the update after *now* (timezone-aware, local).

    Inside quiet hours updates follow *quiet_interval_s*, but the first one after the
    window still comes on time.
    """
    left = quiet.seconds_left(now) if quiet else 0.0
    if left <= 0:
        return seconds_until_next_tick(now.timestamp(), interval_s)
    delay = seconds_until_next_tick(now.timestamp(), quiet_interval_s)
    return max(MIN_GAP_S, min(delay, left + TICK_OFFSET_S))
