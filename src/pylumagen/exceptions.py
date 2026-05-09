"""Exceptions raised by pylumagen.

These map onto Home Assistant's ConfigEntryNotReady / UpdateFailed / etc. in
the `ha-lumagen` integration; see the library's README for the mapping table.
"""

from __future__ import annotations


class LumagenError(Exception):
    """Base exception for all pylumagen errors."""


class LumagenConnectionError(LumagenError):
    """Transport could not establish or maintain a connection.

    Typically maps to ``homeassistant.exceptions.ConfigEntryNotReady`` in the
    HA integration — the device may come back, HA will retry automatically.
    """


class LumagenTimeoutError(LumagenError):
    """A command or expected response did not arrive in time.

    Typically maps to ``UpdateFailed`` in a ``DataUpdateCoordinator``.
    """


class LumagenCommandError(LumagenError):
    """A command was malformed or rejected by the protocol layer.

    Not transport-related — these are programmer errors or bad state.
    """
