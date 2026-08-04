"""Exceptions raised by aiolumagen.

These map onto Home Assistant's ConfigEntryNotReady / etc. in the
`ha-lumagen` integration; see the library's README for the mapping table.

There is deliberately no timeout exception. Nothing in this library is
request/response — commands are fire-and-forget and state arrives whenever
the device sends it — so there is no outstanding request for a timeout to
apply to. Consumers that need a deadline impose it themselves (see
``ha-lumagen``'s config flow, which wraps a state poll in
``asyncio.timeout`` and catches the builtin ``TimeoutError``). A previous
``LumagenTimeoutError`` was advertised in the mapping table for a while
without ever being raised; if the library grows a real ``wait_for_state``
waiter, add it back then and not before.
"""

from __future__ import annotations


class LumagenError(Exception):
    """Base exception for all aiolumagen errors."""


class LumagenConnectionError(LumagenError):
    """Transport could not establish or maintain a connection.

    Typically maps to ``homeassistant.exceptions.ConfigEntryNotReady`` in the
    HA integration — the device may come back, HA will retry automatically.
    """


class LumagenCommandError(LumagenError, ValueError):
    """A command argument was out of range or otherwise unencodable.

    Raised by the command builders in :mod:`aiolumagen.commands` (and the
    client wrappers around them) when a caller passes a value the Lumagen
    has no encoding for — an input outside 1-19, a sharpness level above
    7, an unknown memory letter. These are programmer errors, not device
    or transport failures.

    Also subclasses :class:`ValueError`, which is what these validators
    used to raise. That keeps ``except ValueError`` callers working while
    letting consumers catch the whole library through
    :class:`LumagenError` — the same dual-inheritance trick
    :class:`json.JSONDecodeError` uses. Don't narrow this to
    ``LumagenError`` alone without a major version bump.
    """
