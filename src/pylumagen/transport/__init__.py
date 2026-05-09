"""Transport implementations for pylumagen.

A :class:`LumagenTransport` is a bidirectional byte pipe. Clients send bytes
to the Lumagen via :meth:`write` and receive inbound bytes through a
callback registered at construction. The library ships two transports:

* :class:`SerialTransport` — direct ``pyserial-asyncio`` connection, for
  development against a cabled Lumagen.
* :class:`ESPHomeTransport` — talks through an ESPHome device's
  ``serial_proxy`` entity via ``aioesphomeapi``. This is the production
  path for the ``ha-lumagen`` integration.
"""

from __future__ import annotations

from pylumagen.transport.base import LumagenTransport
from pylumagen.transport.esphome import ESPHomeTransport
from pylumagen.transport.serial import SerialTransport

__all__ = ["ESPHomeTransport", "LumagenTransport", "SerialTransport"]
