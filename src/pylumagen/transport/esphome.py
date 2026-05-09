"""ESPHome serial-proxy transport.

Wraps ``aioesphomeapi``'s serial-proxy APIs so the library can drive a
Lumagen attached to an ESPHome bridge. The transport does not own the
:class:`~aioesphomeapi.APIClient` lifecycle — the caller passes in an
already-connected client (or just host/port) and this class takes care of
picking the right serial-proxy instance, subscribing, and forwarding
bytes.

Protocol notes:

* ``serial_proxy_write`` is fire-and-forget (no await). We don't get any
  per-write error surface; failures show up as connection drops on the
  underlying client.
* Inbound data arrives via :func:`aioesphomeapi.APIClient.subscribe_serial_proxy_data`
  as :class:`aioesphomeapi.SerialProxyDataReceived` messages carrying the
  instance index + bytes. We filter by instance.
* The instance number is the index of the target proxy in
  :attr:`aioesphomeapi.DeviceInfo.serial_proxies`. Callers may specify the
  instance directly or — more robustly — supply a ``name`` to match against
  ``SerialProxyInfo.name``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

from aioesphomeapi import SerialProxyDataReceived, SerialProxyInfo
from aioesphomeapi.client import APIClient
from aioesphomeapi.core import APIConnectionError

from pylumagen.exceptions import LumagenConnectionError
from pylumagen.transport.base import LumagenTransport

_LOGGER = logging.getLogger(__name__)


class ESPHomeTransport(LumagenTransport):
    """Talk to a Lumagen through an ESPHome ``serial_proxy`` entity.

    Two usage patterns:

    1. **Caller-owned APIClient** (preferred in the HA integration): pass an
       already-constructed and logged-in :class:`APIClient` via ``client``.
       This transport will not call ``connect()`` or ``disconnect()`` on it
       — the integration's own config-entry lifecycle owns the client.

    2. **Self-owned APIClient** (useful for scripts and tests): pass
       ``host``/``port``/``noise_psk`` and let the transport manage the
       client. :meth:`connect` will instantiate + log in, and
       :meth:`disconnect` will tear the whole thing down.

    :param instance: 0-based proxy index. Defaults to 0, which matches a
        firmware that declares a single ``serial_proxy`` entry (our case).
    :param name: If provided, overrides ``instance`` by looking up the
        proxy whose ``SerialProxyInfo.name`` matches. Recommended for
        setups with multiple proxies.
    """

    def __init__(
        self,
        *,
        client: APIClient | None = None,
        host: str | None = None,
        port: int = 6053,
        noise_psk: str | None = None,
        password: str | None = None,
        instance: int = 0,
        name: str | None = None,
    ) -> None:
        super().__init__()
        if client is None and host is None:
            raise ValueError("Must provide either `client` or `host`")
        self._owns_client = client is None
        self._client = client or APIClient(
            host or "",
            port,
            password,
            noise_psk=noise_psk,
        )
        self._instance: int = instance
        self._name_hint = name
        self._unsubscribe: Callable[[], None] | None = None
        self._connected = False
        self._disconnected_event = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def instance(self) -> int:
        """Resolved serial-proxy instance index (after :meth:`connect`)."""
        return self._instance

    async def connect(self) -> None:
        if self._connected:
            return
        self._disconnected_event.clear()
        try:
            if self._owns_client:
                await self._client.connect(login=True)
            device_info = await self._client.device_info()
            proxies = getattr(device_info, "serial_proxies", None) or []
            if not proxies:
                raise LumagenConnectionError(
                    "ESPHome device has no serial_proxy entities; "
                    "is the firmware up to date?"
                )
            self._instance = self._resolve_instance(proxies)
            self._unsubscribe = self._client.subscribe_serial_proxy_data(self._on_data_msg)
            self._client.serial_proxy_subscribe(self._instance)
        except APIConnectionError as err:
            await self._teardown_owned_client_on_error()
            raise LumagenConnectionError(f"ESPHome API connection failed: {err}") from err
        except LumagenConnectionError:
            await self._teardown_owned_client_on_error()
            raise
        except Exception as err:
            await self._teardown_owned_client_on_error()
            raise LumagenConnectionError(
                f"Unexpected error connecting ESPHome transport: {err}"
            ) from err
        self._connected = True
        _LOGGER.debug(
            "ESPHome serial-proxy transport connected (instance=%d)", self._instance
        )

    async def disconnect(self) -> None:
        if self._unsubscribe is not None:
            try:
                self._unsubscribe()
            except Exception:
                _LOGGER.debug("Error while unsubscribing serial proxy", exc_info=True)
            self._unsubscribe = None
        if self._connected:
            try:
                self._client.serial_proxy_unsubscribe(self._instance)
            except Exception:
                _LOGGER.debug("Error while sending serial_proxy_unsubscribe", exc_info=True)
        if self._owns_client:
            try:
                await self._client.disconnect()
            except Exception:
                _LOGGER.debug("Error while closing owned APIClient", exc_info=True)
        self._connected = False
        self._disconnected_event.set()

    async def write(self, data: bytes) -> None:
        if not self._connected:
            raise LumagenConnectionError("ESPHome transport is not connected")
        try:
            self._client.serial_proxy_write(self._instance, data)
        except APIConnectionError as err:
            self._connected = False
            raise LumagenConnectionError(f"Serial proxy write failed: {err}") from err

    # ------------------------------------------------------------------
    # Internal plumbing
    # ------------------------------------------------------------------

    def _resolve_instance(self, proxies: list[SerialProxyInfo]) -> int:
        """Pick the right proxy index from the device's SerialProxyInfo list."""
        if self._name_hint is not None:
            for idx, info in enumerate(proxies):
                if getattr(info, "name", None) == self._name_hint:
                    return idx
            available = ", ".join(
                repr(getattr(p, "name", "?")) for p in proxies
            ) or "<none>"
            raise LumagenConnectionError(
                f"No serial_proxy named {self._name_hint!r} on ESPHome device "
                f"(available: {available})"
            )
        if self._instance < 0 or self._instance >= len(proxies):
            raise LumagenConnectionError(
                f"serial_proxy instance {self._instance} out of range "
                f"(device exposes {len(proxies)})"
            )
        return self._instance

    def _on_data_msg(self, msg: SerialProxyDataReceived) -> None:
        if msg.instance != self._instance:
            return
        self._emit(bytes(msg.data))

    async def _teardown_owned_client_on_error(self) -> None:
        if self._owns_client:
            with suppress(Exception):  # pragma: no cover - best-effort cleanup
                await self._client.disconnect()
