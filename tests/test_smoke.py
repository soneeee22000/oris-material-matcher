import asyncio
import socket

import pytest
from pytest_socket import SocketConnectBlockedError

import oris_matcher


def test_package_imports() -> None:
    assert oris_matcher.__version__


async def test_event_loop_runs_with_sockets_disabled() -> None:
    await asyncio.sleep(0)


def test_external_network_is_blocked() -> None:
    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(("1.1.1.1", 443), timeout=2)
