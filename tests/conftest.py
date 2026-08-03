"""Shared pytest fixtures for pylumagen tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

S01_RESPONSE = b"!S01,FakeModel,000000,0000,000000\r\n"
"""Device-info reply: model name, software rev, model number, serial."""

I25_RESPONSE = b"!I25,0,000,0000,0,0,000,000\r\n"
"""Deliberately minimal Full v5 status reply — same zeroed shape the protocol
tests use for a neutral status line.

The fake has to answer ``ZQI25`` at all because Full v5 is the library's
supported floor: a transport that stays silent is simulating unsupported
firmware, and the startup handshake would (correctly) wait out its status
window in every test that calls ``start()``.

It answers *minimally* on purpose. The trailing v5 fields — power at index 24,
input memory at 23 — are omitted rather than set, because these are
client-orchestration tests: several of them establish their own power state
via ``feed()`` and assert on the transitions. A fixture that declared the
device powered on would silently invalidate those premises (the protocol
layer dedupes no-op updates, so a later ``!S02,1`` would stop firing a
callback at all). Rich, realistic payloads belong in ``test_protocol.py``,
which feeds recorded lines; here the only requirement is a non-empty status
payload so the handshake knows v5 answered.
"""


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
        if self._on_data is None:
            return
        # Auto-respond to the two handshake queries the client waits on, so
        # startup completes immediately instead of burning its retry and
        # status windows in every test.
        if data == b"ZQS01":
            self._on_data(S01_RESPONSE)
        elif data == b"ZQI25":
            self._on_data(I25_RESPONSE)

    def feed(self, data: bytes | str) -> None:
        """Simulate inbound bytes from the Lumagen."""
        if isinstance(data, str):
            data = data.encode("ascii")
        if self._on_data is not None and data:
            self._on_data(data)


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()
