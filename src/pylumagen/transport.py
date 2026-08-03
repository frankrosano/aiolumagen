"""Byte-level transport for pylumagen.

Wraps :mod:`serialx`'s :func:`create_serial_connection` so the library can
open any serial URL scheme serialx supports:

* ``/dev/tty.usbserial-*`` — direct USB/RS-232 serial
* ``esphome://<host>:<port>/?port_name=<name>&key=<psk>`` — ESPHome
  serial proxy
* ``socket://<host>:<port>`` — raw TCP bridge (e.g. ser2net)
* anything else serialx grows support for

The ``esphome://`` scheme needs ``aioesphomeapi``, which this package does
*not* depend on directly — see the note on ``dependencies`` in
``pyproject.toml``. Inside Home Assistant it's already present; elsewhere,
install ``pylumagen[esphome]``. serialx registers its platforms
conditionally, so the other schemes work fine without it — and a missing
install surfaces as a :class:`~pylumagen.exceptions.LumagenConnectionError`
from :meth:`connect` naming the extra (serialx's own message for this is
"No handler registered for URI scheme", which doesn't hint at the cause; see
:meth:`LumagenTransport._connect_error_message`).

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
import importlib.util
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
            raise LumagenConnectionError(self._connect_error_message(err)) from err
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

    def _connect_error_message(self, err: Exception) -> str:
        """Explain a connect failure, translating the missing-extra case.

        serialx registers its platforms *conditionally*: with ``aioesphomeapi``
        absent, the ``esphome://`` scheme simply never registers, and the
        failure surfaces as "No handler registered for URI scheme" rather than
        an ImportError. That phrasing gives a user no idea what to install, so
        detect the real cause — an ``esphome://`` URL with the package missing
        — and say so.

        Checked with :func:`importlib.util.find_spec` rather than a real
        import: this runs on a failure path and there's no reason to pay for
        loading a heavyweight compiled package just to prove it exists.
        """
        base = f"Could not open {self.url!r}: {err}"
        if not self.url.lower().startswith("esphome://"):
            return base
        if importlib.util.find_spec("aioesphomeapi") is not None:
            return base
        return (
            f"Could not open {self.url!r}: the ESPHome transport needs the "
            "'aioesphomeapi' package, which isn't installed — so serialx never "
            "registered the esphome:// scheme, which is what the underlying "
            f"error ({err}) actually means. Install pylumagen[esphome], or "
            "inside Home Assistant make sure the ESPHome integration is set "
            "up; it provides that package, and pylumagen deliberately doesn't "
            "depend on it so it can't fight Home Assistant's pinned version."
        )

    def _emit(self, data: bytes) -> None:
        if self._on_data is not None and data:
            self._on_data(data)

    def _handle_connection_lost(self) -> None:
        self._connected = False
        self._transport = None
