import io
import logging
import os
import shutil
import socket
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from zurich_air.systemd import JournalFormatter, Notifier, logs_to_journal

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")


@pytest.fixture
def short_dir():
    # AF_UNIX paths are limited to about 104 bytes, which pytest's tmp_path can exceed.
    path = Path(tempfile.mkdtemp(prefix="za-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def receiver(short_dir):
    """A datagram socket standing in for systemd's notification socket."""
    path = short_dir / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.bind(str(path))
        sock.settimeout(2)
        yield str(path), sock


def test_without_notify_socket_nothing_is_sent():
    notifier = Notifier(env={})
    assert not notifier.enabled
    assert notifier.ready() is False
    assert notifier.watchdog() is False
    assert notifier.stopping() is False


def test_sends_state_changes_to_the_socket(receiver):
    path, sock = receiver
    notifier = Notifier(env={"NOTIFY_SOCKET": path})
    assert notifier.enabled
    assert notifier.ready()
    assert notifier.watchdog()
    assert notifier.stopping()
    assert [sock.recv(256) for _ in range(3)] == [b"READY=1", b"WATCHDOG=1", b"STOPPING=1"]


def test_several_assignments_go_in_one_datagram(receiver):
    path, sock = receiver
    assert Notifier(env={"NOTIFY_SOCKET": path}).notify("READY=1", "STATUS=running")
    assert sock.recv(256) == b"READY=1\nSTATUS=running"


@linux_only
def test_abstract_socket_address():
    name = f"zurich-air-test-{uuid.uuid4().hex}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.bind("\0" + name)
        sock.settimeout(2)
        assert Notifier(env={"NOTIFY_SOCKET": "@" + name}).watchdog()
        assert sock.recv(256) == b"WATCHDOG=1"


def test_a_missing_socket_never_raises(short_dir):
    notifier = Notifier(env={"NOTIFY_SOCKET": str(short_dir / "gone")})
    assert notifier.enabled
    assert notifier.watchdog() is False


def test_unsupported_socket_kinds_are_ignored():
    assert not Notifier(env={"NOTIFY_SOCKET": "vsock:2:1234"}).enabled


@pytest.fixture
def stream(tmp_path):
    with (tmp_path / "stderr").open("w") as fh:
        yield fh


def journal_env(fh):
    stat = os.fstat(fh.fileno())
    return {"JOURNAL_STREAM": f"{stat.st_dev}:{stat.st_ino}"}


def test_journal_stream_detected_by_device_and_inode(stream, tmp_path):
    assert logs_to_journal(stream, journal_env(stream))
    with (tmp_path / "other").open("w") as other:
        assert not logs_to_journal(stream, journal_env(other))  # inherited by a child


@pytest.mark.parametrize("value", [None, "", "garbage", "1:2:3"])
def test_no_journal_without_a_valid_journal_stream(stream, value):
    env = {} if value is None else {"JOURNAL_STREAM": value}
    assert not logs_to_journal(stream, env)


def test_no_journal_for_streams_without_a_file():
    assert not logs_to_journal(io.StringIO(), {"JOURNAL_STREAM": "1:2"})


@pytest.mark.parametrize(
    ("level", "prefix"),
    [
        (logging.DEBUG, "<7>"),
        (logging.INFO, "<6>"),
        (logging.WARNING, "<4>"),
        (logging.ERROR, "<3>"),
        (logging.CRITICAL, "<2>"),
    ],
)
def test_journal_formatter_sets_the_priority(level, prefix):
    record = logging.LogRecord("zurich_air.app", level, __file__, 1, "hello %s", ("you",), None)
    line = JournalFormatter("%(levelname)s %(name)s: %(message)s").format(record)
    assert line == f"{prefix}{logging.getLevelName(level)} zurich_air.app: hello you"
