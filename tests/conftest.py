"""Shared test setup: the suite never touches the network."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def refuse_connections(monkeypatch):
    """Fail every outgoing TCP connection (urllib connects through this), so that a test
    that forgets to replace a data source fails soft and fast instead of going online."""

    def refuse(address, *args, **kwargs):
        raise OSError(f"tests must not connect to {address}")

    monkeypatch.setattr(socket, "create_connection", refuse)
