"""Direct serial transport via pyserial-asyncio.

Useful for development against a cabled Lumagen and for integration tests
that don't involve an ESPHome bridge. The production path in ha-lumagen
uses :class:`~pylumagen.transport.esphome.ESPHomeTransport`.
"""

from __future__ import annotations

import asyncio
import logging

import serial_asyncio  # type: ignore[import-untyped]

from pylumagen.exceptions import LumagenConnectionError
from pylumagen.transport.base import LumagenTransport

_LOGGER = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 9600


class _LumagenProtocol(asyncio.Protocol):
    """asyncio protocol bridge between pyserial-asyncio and our transport."""

    def __init__(self, owner: SerialTransport) -> None:
        self._owner = owner

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        _LOGGER.debug("Serial connection established")

    def data_received(self, data: bytes) -> None:
        self._owner._emit(data)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("Serial connection closed: %s", exc)
        self._owner._handle_connection_lost()


class SerialTransport(LumagenTransport):
    """Talk to a Lumagen over a local serial device node.

    :param url: pyserial URL — typically a device path like
        ``/dev/tty.usbserial-ABCD``. Also accepts loop:// and socket:// URLs
        for tests.
    :param baudrate: Defaults to 9600 (the Lumagen's hardcoded rate).
    """

    def __init__(self, url: str, *, baudrate: int = DEFAULT_BAUDRATE) -> None:
        super().__init__()
        self._url = url
        self._baudrate = baudrate
        self._transport: asyncio.Transport | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_running_loop()
        try:
            self._transport, _ = await serial_asyncio.create_serial_connection(
                loop,
                lambda: _LumagenProtocol(self),
                self._url,
                baudrate=self._baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
            )
        except Exception as err:
            raise LumagenConnectionError(
                f"Could not open serial port {self._url}: {err}"
            ) from err
        self._connected = True

    async def disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._connected = False

    async def write(self, data: bytes) -> None:
        if not self._connected or self._transport is None:
            raise LumagenConnectionError("Serial transport is not connected")
        self._transport.write(data)

    def _handle_connection_lost(self) -> None:
        self._connected = False
        self._transport = None
