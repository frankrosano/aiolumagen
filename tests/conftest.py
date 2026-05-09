"""Shared pytest fixtures for pylumagen tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from pylumagen.transport.base import LumagenTransport


class FakeTransport(LumagenTransport):
    """In-memory transport for integration-style tests.

    Tests push inbound bytes with :meth:`feed` and inspect outbound writes
    via :attr:`sent`. No asyncio timing involved — everything resolves
    synchronously inside the ``async`` wrapper.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[bytes] = []
        self._connected = False
        self.connect_hook: Callable[[], Any] | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        if self.connect_hook is not None:
            result = self.connect_hook()
            if asyncio.iscoroutine(result):
                await result

    async def disconnect(self) -> None:
        self._connected = False

    async def write(self, data: bytes) -> None:
        self.sent.append(data)

    def feed(self, data: bytes | str) -> None:
        """Simulate inbound bytes from the Lumagen."""
        if isinstance(data, str):
            data = data.encode("ascii")
        self._emit(data)


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()
