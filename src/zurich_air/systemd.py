"""The two bits of systemd integration the service needs, using only the standard library.

* :class:`Notifier` speaks the ``sd_notify`` protocol: one datagram per message to the
  AF_UNIX socket named in ``$NOTIFY_SOCKET`` (``READY=1``, ``WATCHDOG=1``, …). Outside
  systemd the variable is unset and every call does nothing.
* :func:`logs_to_journal` tells whether stderr is connected to the journal, and
  :class:`JournalFormatter` formats log records for it.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Mapping
from typing import IO

log = logging.getLogger(__name__)


class Notifier:
    """Sends state changes to the service manager; a no-op without ``$NOTIFY_SOCKET``."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        raw = (os.environ if env is None else env).get("NOTIFY_SOCKET", "")
        self.address: str | None = None
        if raw.startswith("@"):  # abstract namespace (Linux)
            self.address = "\0" + raw[1:]
        elif raw.startswith("/"):
            self.address = raw
        elif raw:
            log.debug("ignoring unsupported NOTIFY_SOCKET %r", raw)

    @property
    def enabled(self) -> bool:
        return self.address is not None

    def notify(self, *assignments: str) -> bool:
        """Send ``KEY=value`` lines in one datagram; True if it was sent.

        Never raises: a failed notification must not take the display down with it.
        """
        if self.address is None:
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.sendto("\n".join(assignments).encode(), self.address)
        except OSError as exc:
            log.debug("sd_notify failed: %s", exc)
            return False
        return True

    def ready(self) -> bool:
        return self.notify("READY=1")

    def watchdog(self) -> bool:
        return self.notify("WATCHDOG=1")

    def stopping(self) -> bool:
        return self.notify("STOPPING=1")


def logs_to_journal(stream: IO[str], env: Mapping[str, str] | None = None) -> bool:
    """Whether *stream* is the journal stream systemd announced in ``$JOURNAL_STREAM``.

    The variable is inherited by child processes, so the device and inode are compared
    with those of *stream*, as systemd.exec(5) recommends.
    """
    value = (os.environ if env is None else env).get("JOURNAL_STREAM", "")
    try:
        device, inode = (int(part) for part in value.split(":"))
        stat = os.fstat(stream.fileno())
    except (ValueError, OSError, AttributeError):  # unset, malformed, or no real file
        return False
    return (stat.st_dev, stat.st_ino) == (device, inode)


class JournalFormatter(logging.Formatter):
    """Prefixes each record with its syslog priority (``<4>`` for a warning), which
    journald removes and records, so that ``journalctl -p warning`` finds problems. Leave
    the time out of the format: journald adds its own."""

    PRIORITIES = (
        (logging.CRITICAL, 2),
        (logging.ERROR, 3),
        (logging.WARNING, 4),
        (logging.INFO, 6),
    )

    def format(self, record: logging.LogRecord) -> str:
        priority = next((p for level, p in self.PRIORITIES if record.levelno >= level), 7)
        return f"<{priority}>{super().format(record)}"
