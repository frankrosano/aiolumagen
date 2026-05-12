"""High-level Lumagen client.

Composes a :class:`~pylumagen.transport.base.LumagenTransport` with a
:class:`~pylumagen.protocol.LumagenProtocol` and exposes the commands a
typical consumer (the ``ha-lumagen`` HA integration, tests, scripts)
needs. Implements the startup handshake (``ZE2`` + initial status
queries) and runs a background poll loop so unsolicited reports aren't
the only way to stay in sync.

The old ESPHome firmware waited 18 s on boot for the FTDI chip to
enumerate before sending anything. That protection is optional here
(see ``startup_delay`` on :class:`LumagenClient`) because in every
non-cold-boot case the ESP is already enumerated by the time we
attach. Default is no delay; bump it if you hit reconnect races.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from pylumagen.commands import ECHO_OFF_WITH_STATUS, Query, input_command
from pylumagen.exceptions import LumagenConnectionError, LumagenError
from pylumagen.protocol import LumagenProtocol
from pylumagen.state import LumagenState

_LOGGER = logging.getLogger(__name__)


class _TransportLike(Protocol):
    """Minimal transport contract the client depends on.

    Concrete implementations: :class:`pylumagen.transport.LumagenTransport`
    for real connections, and a lightweight in-memory fake in
    ``tests/conftest.py`` for unit tests. The contract is intentionally
    duck-typed — no ABC — because there's only ever one real transport.
    """

    @property
    def connected(self) -> bool: ...

    def set_data_callback(self, callback: Callable[[bytes], None]) -> None: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def write(self, data: bytes) -> None: ...


StateListener = Callable[[LumagenState, tuple[str, ...]], None]
"""Synchronous listener. Called inline from the protocol layer."""

AsyncStateListener = Callable[[LumagenState, tuple[str, ...]], Awaitable[None]]
"""Async listener. Scheduled as a task when state changes."""


class LumagenClient:
    """Public entry point for talking to a Lumagen Radiance Pro.

    :param transport: Pre-constructed transport. Its data callback will be
        wired during :meth:`start`.
    :param startup_delay: Seconds to wait after ``transport.connect()``
        before sending anything. Defaults to 0 (no wait) — the ESP is
        assumed to be already enumerated by the time a client attaches,
        which matches every real-world case except a cold boot.
        If you hit "ZE2 sent before FTDI enumeration finishes" issues
        after an ESP reboot, bump this (e.g. 18 s was the old firmware's
        cold-boot value).
    :param power_poll_interval: Seconds between background power polls.
        ``None`` disables polling entirely (pure push mode).
    :param status_poll_interval: Seconds between ``ZQI24`` polls when the
        Lumagen reports power on. ``None`` disables.
    """

    def __init__(
        self,
        transport: _TransportLike,
        *,
        startup_delay: float = 0.0,
        power_poll_interval: float | None = 60.0,
        status_poll_interval: float | None = 60.0,
        stale_timeout: float = 120.0,
    ) -> None:
        self._transport = transport
        self._startup_delay = startup_delay
        self._power_poll_interval = power_poll_interval
        self._status_poll_interval = status_poll_interval
        self._stale_timeout = stale_timeout
        self._protocol = LumagenProtocol(self._on_protocol_update)
        self._listeners: list[StateListener] = []
        self._async_listeners: list[AsyncStateListener] = []
        self._listener_tasks: set[asyncio.Task[None]] = set()
        self._poll_task: asyncio.Task[None] | None = None
        self._started = False
        self._last_response_time: float | None = None
        self._available = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect the transport, run the startup handshake, begin polling.

        Safe to call once. Subsequent calls are no-ops until :meth:`stop`.
        """
        if self._started:
            return
        self._transport.set_data_callback(self._protocol.feed_bytes)
        await self._transport.connect()
        self._started = True
        if self._startup_delay > 0:
            _LOGGER.debug("Waiting %.1fs for Lumagen warmup", self._startup_delay)
            await asyncio.sleep(self._startup_delay)
        await self._send_startup_sequence()
        if (
            self._power_poll_interval is not None
            or self._status_poll_interval is not None
        ):
            self._poll_task = asyncio.create_task(
                self._poll_loop(), name="pylumagen-poll"
            )

    async def stop(self) -> None:
        """Cancel polling and disconnect the transport. Idempotent."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        await self._transport.disconnect()
        self._started = False

    @property
    def state(self) -> LumagenState:
        """Latest accumulated state snapshot."""
        return self._protocol.state

    @property
    def connected(self) -> bool:
        return self._transport.connected

    @property
    def available(self) -> bool:
        """True when the Lumagen is actively responding.

        Becomes True on the first successful response, and reverts to False
        if no response has been received within ``stale_timeout`` seconds
        (default 120s = 2 missed poll cycles). This lets HA mark entities
        as unavailable when the serial channel is lost mid-session.
        """
        if not self._available:
            return False
        if self._last_response_time is None:
            return False
        elapsed = asyncio.get_event_loop().time() - self._last_response_time
        return elapsed < self._stale_timeout

    # ------------------------------------------------------------------
    # Listeners
    # ------------------------------------------------------------------

    def subscribe(self, listener: StateListener) -> Callable[[], None]:
        """Register a sync listener; returns an unsubscribe callable."""
        self._listeners.append(listener)
        def _unsubscribe() -> None:
            with suppress(ValueError):
                self._listeners.remove(listener)
        return _unsubscribe

    def subscribe_async(self, listener: AsyncStateListener) -> Callable[[], None]:
        """Register an async listener. Scheduled as a task per update."""
        self._async_listeners.append(listener)
        def _unsubscribe() -> None:
            with suppress(ValueError):
                self._async_listeners.remove(listener)
        return _unsubscribe

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def send_command(self, cmd: str, *, cr: bool = False) -> None:
        """Send a raw command string.

        :param cmd: The command characters (no terminator; one is appended
            if ``cr=True``).
        :param cr: Append a carriage return. Only a handful of Lumagen
            commands need this — consult the RS-232 doc before setting it.
        """
        if not self._started:
            raise LumagenError("LumagenClient.send_command called before start()")
        payload = cmd.encode("ascii")
        if cr:
            payload += b"\r"
        _LOGGER.debug("TX: %s%s", cmd, "<CR>" if cr else "")
        await self._transport.write(payload)

    async def power_on(self) -> None:
        await self.send_command("%")

    async def standby(self) -> None:
        await self.send_command("$")

    async def set_input(self, n: int) -> None:
        """Select input ``n`` (1-19)."""
        await self.send_command(input_command(n))

    async def query_alive(self) -> None:
        await self.send_command(Query.ALIVE.value)

    async def query_device_info(self) -> None:
        await self.send_command(Query.DEVICE_INFO.value)

    async def query_power(self) -> None:
        await self.send_command(Query.POWER.value)

    async def query_input_info(self) -> None:
        await self.send_command(Query.INPUT_INFO.value)

    async def query_full_status(self) -> None:
        await self.send_command(Query.FULL_STATUS.value)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _send_startup_sequence(self) -> None:
        """ZE2 echo-off, then initial queries with retry.

        The ESP's USB-UART channel may not be ready immediately after boot
        (FTDI enumeration takes ~10-15s). Commands sent before the channel
        initializes are silently dropped. We retry the handshake up to
        ``_startup_max_retries`` times, waiting ``_startup_retry_interval``
        between attempts, checking for a ``!S01`` response (model field
        populated) as the success signal.
        """
        max_retries = 5
        retry_interval = 4.0  # seconds between attempts

        for attempt in range(max_retries):
            try:
                await self.send_command(ECHO_OFF_WITH_STATUS)
                await asyncio.sleep(0.3)
                await self.query_device_info()
                await asyncio.sleep(0.3)
                await self.query_power()
                await asyncio.sleep(0.3)
                await self.query_input_info()
                await asyncio.sleep(0.3)
                await self.query_full_status()
            except LumagenConnectionError:
                _LOGGER.warning("Lumagen startup queries aborted - transport disconnected")
                return

            # Wait for !S01 to confirm the Lumagen actually received our queries.
            deadline = asyncio.get_running_loop().time() + retry_interval
            while asyncio.get_running_loop().time() < deadline:
                if self._protocol.state.model is not None:
                    _LOGGER.debug(
                        "Lumagen startup handshake succeeded on attempt %d", attempt + 1
                    )
                    return
                await asyncio.sleep(0.2)

            if attempt < max_retries - 1:
                _LOGGER.debug(
                    "Lumagen startup attempt %d/%d got no response, retrying",
                    attempt + 1,
                    max_retries,
                )

        _LOGGER.warning(
            "Lumagen startup handshake: no response after %d attempts "
            "(%.0fs total). The poll loop will continue trying.",
            max_retries,
            max_retries * retry_interval,
        )

    async def _poll_loop(self) -> None:
        """Poll at the shorter of the two intervals; gate ZQI24 on power.

        Also checks for staleness: if no response has been received within
        ``stale_timeout``, marks the client as unavailable and notifies
        listeners with a synthetic update so the coordinator can mark
        entities unavailable in HA.
        """
        p_iv = self._power_poll_interval
        s_iv = self._status_poll_interval
        base = min(v for v in (p_iv, s_iv) if v is not None)
        p_due = asyncio.get_running_loop().time() + (p_iv or 0)
        s_due = asyncio.get_running_loop().time() + (s_iv or 0)
        try:
            while True:
                await asyncio.sleep(base)
                now = asyncio.get_running_loop().time()
                try:
                    if p_iv is not None and now >= p_due:
                        await self.query_power()
                        p_due = now + p_iv
                    if (
                        s_iv is not None
                        and now >= s_due
                        and self.state.power_on is True
                    ):
                        await self.query_full_status()
                        s_due = now + s_iv
                except LumagenConnectionError as err:
                    _LOGGER.warning("Lumagen poll failed: %s", err)

                # Staleness check: if we were available but haven't heard
                # back in stale_timeout seconds, mark unavailable and
                # notify listeners so HA can react.
                if self._available and not self.available:
                    self._available = False
                    _LOGGER.warning(
                        "Lumagen is now unavailable (no response in %.0fs)",
                        self._stale_timeout,
                    )
                    # Fire a synthetic update so the coordinator sees the
                    # availability change and marks entities unavailable.
                    for listener in list(self._listeners):
                        with suppress(Exception):
                            listener(self._protocol.state, ("_unavailable",))
        except asyncio.CancelledError:
            raise

    def _on_protocol_update(self, state: LumagenState, codes: tuple[str, ...]) -> None:
        self._last_response_time = asyncio.get_event_loop().time()
        if not self._available:
            self._available = True
            _LOGGER.info("Lumagen is now available (first response received)")
        for listener in list(self._listeners):
            try:
                listener(state, codes)
            except Exception:
                _LOGGER.exception("Sync state listener raised; dropping it")
                with suppress(ValueError):
                    self._listeners.remove(listener)
        for async_listener in list(self._async_listeners):
            task = asyncio.create_task(
                self._run_async_listener(async_listener, state, codes),
                name="pylumagen-listener",
            )
            self._listener_tasks.add(task)
            task.add_done_callback(self._listener_tasks.discard)

    async def _run_async_listener(
        self,
        listener: AsyncStateListener,
        state: LumagenState,
        codes: tuple[str, ...],
    ) -> None:
        try:
            await listener(state, codes)
        except Exception:
            _LOGGER.exception("Async state listener raised; dropping it")
            with suppress(ValueError):
                self._async_listeners.remove(listener)
