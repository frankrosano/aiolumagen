"""Shared pytest fixtures for pylumagen tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest


class FakeTransport:
    """In-memory transport stub that implements the duck-typed client contract.

    Tests push inbound bytes with :meth:`feed` and inspect outbound writes
    via :attr:`sent`. No asyncio scheduling involved — everything resolves
    synchronously inside the ``async`` wrapper.
    """

    def __init__(self) -> None:
        self._on_data: Callable[[bytes], None] | None = None
        self.sent: list[bytes] = []
        self._connected = False
        self.connect_hook: Callable[[], Any] | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    def set_data_callback(self, callback: Callable[[bytes], None]) -> None:
        self._on_data = callback

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
        if self._on_data is not None and data:
            self._on_data(data)


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()
