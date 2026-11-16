"""The unit-test network guard: local transports allowed, external ones refused.

Windows implements ``socket.socketpair`` — asyncio's event-loop self-pipe — by
connecting to a loopback listener, so the guard must let local connections
through or every async unit test errors during setup.
"""

from __future__ import annotations

import socket

import pytest

pytestmark = pytest.mark.unit


def test_loopback_connections_are_allowed() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=1)
        client.close()
    finally:
        server.close()


def test_socketpair_is_allowed() -> None:
    left, right = socket.socketpair()
    try:
        left.sendall(b"ping")
        assert right.recv(4) == b"ping"
    finally:
        left.close()
        right.close()


def test_external_connections_are_refused() -> None:
    client = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="network access is forbidden"):
            client.connect(("203.0.113.1", 9))
    finally:
        client.close()
