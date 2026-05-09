"""Byte-level transport for pylumagen.

Wraps :mod:`serialx`'s :func:`create_serial_connection` so the library can
open any serial URL scheme serialx supports:

* ``/dev/tty.usbserial-*`` — direct USB/RS-232 serial
* ``esphome://<host>:<port>/?port_name=<name>&key=<psk>`` — ESPHome
  serial proxy
* ``socket://<host>:<port>`` — raw TCP bridge (e.g. ser2net)
* anything else serialx grows support for

:class:`LumagenTransport` is deliberately thin: it opens the URL, pipes
inbound bytes to a callback, and exposes :meth:`write`. All Lumagen
protocol concerns live in :class:`~pylumagen.protocol.LumagenProtocol`.

For tests we use a lightweight in-memory stub (see ``tests/conftest.py``)
that implements the same three public methods (``connect`` / ``disconnect``
/ ``write``) without touching serialx. No ABC is exposed — duck typing
keeps the surface small.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import serialx

from pylumagen.exceptions import LumagenConnectionError

_LOGGER = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 9600


class _BridgeProtocol(asyncio.Protocol):
    """asyncio.Protocol that fans inbound bytes to the owning transport."""

    def __init__(self, owner: LumagenTransport) -> None:
        self._owner = owner

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        _LOGGER.debug("Serial connection established via %s", self._owner.url)

    def data_received(self, data: bytes) -> None:
        self._owner._emit(data)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("Serial connection closed (%s): %s", self._owner.url, exc)
        self._owner._handle_connection_lost()


class LumagenTransport:
    """Open a serialx URL and expose read-callback + write-bytes API.

    :param url: Any URL accepted by :func:`serialx.serial_for_url`. For
        a Lumagen attached to an ESPHome bridge, this will typically be
        ``esphome://<host>:<port>/?port_name=<name>&key=<psk>``.
    :param baudrate: Defaults to 9600 (the Lumagen's hardcoded rate).
    """

    def __init__(self, url: str, *, baudrate: int = DEFAULT_BAUDRATE) -> None:
        self.url = url
        self.baudrate = baudrate
        self._on_data: Callable[[bytes], None] | None = None
        self._transport: asyncio.Transport | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def set_data_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register the inbound-data callback. Must be set before :meth:`connect`."""
        self._on_data = callback

    async def connect(self) -> None:
        if self._connected:
            return
        loop = asyncio.get_running_loop()
        try:
            transport, _protocol = await serialx.create_serial_connection(
                loop,
                lambda: _BridgeProtocol(self),
                url=self.url,
                baudrate=self.baudrate,
            )
        except Exception as err:
            raise LumagenConnectionError(
                f"Could not open {self.url!r}: {err}"
            ) from err
        self._transport = transport
        self._connected = True

    async def disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self._connected = False

    async def write(self, data: bytes) -> None:
        if not self._connected or self._transport is None:
            raise LumagenConnectionError("Transport is not connected")
        self._transport.write(data)

    # -- Internal plumbing -------------------------------------------------

    def _emit(self, data: bytes) -> None:
        if self._on_data is not None and data:
            self._on_data(data)

    def _handle_connection_lost(self) -> None:
        self._connected = False
        self._transport = None
