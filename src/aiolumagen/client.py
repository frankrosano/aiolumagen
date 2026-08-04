"""High-level Lumagen client.

Composes a :class:`~aiolumagen.transport.LumagenTransport` with a
:class:`~aiolumagen.protocol.LumagenProtocol` and exposes the commands a
typical consumer (the ``ha-lumagen`` HA integration, tests, scripts)
needs. Implements the startup handshake (``ZE2`` + initial status
queries) and runs a background poll loop so unsolicited reports aren't
the only way to stay in sync.

The startup handshake is built around two short retries (2 attempts at
1.5 s apart) rather than a long up-front wait: the RS-232 link is always
up as soon as the transport connects, so in steady-state operation the
first attempt succeeds and the retry only matters when we attach while
the bridge is still booting.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol

from aiolumagen.commands import (
    ECHO_OFF_WITH_STATUS,
    Power,
    Query,
    fan_speed_command,
    game_mode_command,
    hdr_intensity_mapping_command,
    input_command,
    reset_auto_aspect_command,
    sharpness_command,
    subtitle_shift_command,
)
from aiolumagen.exceptions import (
    LumagenCommandError,
    LumagenConnectionError,
    LumagenError,
)
from aiolumagen.protocol import LumagenProtocol
from aiolumagen.state import HdrGammaMode, LumagenState, SharpnessSensitivity

_LOGGER = logging.getLogger(__name__)


class _TransportLike(Protocol):
    """Minimal transport contract the client depends on.

    Concrete implementations: :class:`aiolumagen.transport.LumagenTransport`
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
"""Synchronous listener. Called inline from the protocol layer.

Listeners are called from the transport's data callback, so they must not
block or await. A consumer needing async work should schedule it itself —
HA's ``async_set_updated_data`` is the model: cheap, synchronous, and it
hands off to the event loop on its own terms.
"""


class LumagenClient:
    """Public entry point for talking to a Lumagen Radiance Pro.

    :param transport: Pre-constructed transport. Its data callback will be
        wired during :meth:`start`.
    :param power_poll_interval: Seconds between background power polls.
        ``None`` disables polling entirely (pure push mode).
    :param status_poll_interval: Seconds between ``ZQI25`` polls when the
        Lumagen reports power on. ``None`` disables.
    :param stale_timeout: Seconds without inbound bytes after which the
        client marks itself unavailable and forces a transport reconnect.
        Must be greater than the longest poll interval — see the
        constructor's ValueError below for details.
    """

    # Delay (seconds, after a control command) at which we re-poll the
    # Lumagen as a backstop. With "Full v5" reporting enabled (the
    # documented happy path — Tip0011, Report mode changes -> Full v5)
    # the device pushes !I25 on every state change INCLUDING power
    # transitions, so this tick is purely a safety net for users still
    # on Full v4 or older, where power on/off can lag a poll cycle.
    # Empty by default since the recommended setup doesn't need it; the
    # tick mechanism stays in place so future Lumagen quirks can re-arm
    # it without code changes.
    REFRESH_TICKS: tuple[float, ...] = ()

    FULL_STATUS_WAIT = 2.0
    """Seconds allowed for the ``!I25`` reply once the device is known to talk.

    The reply is a ~25-field line — roughly 100 ms of wire time at 9600 baud,
    plus device processing, plus (on an ESPHome bridge) a network hop and up
    to one firmware loop iteration. Generous on purpose: this only costs the
    full window when Full v5 genuinely doesn't answer, and being stingy here
    means the pre-v5 warning fires on healthy devices.
    """

    def __init__(
        self,
        transport: _TransportLike,
        *,
        power_poll_interval: float | None = 60.0,
        status_poll_interval: float | None = 60.0,
        stale_timeout: float = 90.0,
    ) -> None:
        # Invariant: stale_timeout MUST be greater than the poll interval.
        # The poll loop checks staleness immediately after sending a query,
        # before the device's response can arrive — if the timeout is shorter
        # than one poll cycle, every cycle's elapsed-since-last-response will
        # exceed it and trigger a false-positive reconnect. (Bug observed in
        # 0.1.0 with stale_timeout=45s vs. 60s polls: warnings every 60s in
        # steady state.)
        poll_intervals = [
            iv for iv in (power_poll_interval, status_poll_interval) if iv is not None
        ]
        if poll_intervals and stale_timeout <= max(poll_intervals):
            raise ValueError(
                f"stale_timeout ({stale_timeout}s) must be greater than the "
                f"longest poll interval ({max(poll_intervals)}s); otherwise "
                f"every poll cycle will trip a false-positive reconnect."
            )
        self._transport = transport
        self._power_poll_interval = power_poll_interval
        self._status_poll_interval = status_poll_interval
        self._stale_timeout = stale_timeout
        self._protocol = LumagenProtocol(self._on_protocol_update)
        self._listeners: list[StateListener] = []
        self._poll_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
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
        # Wrap the protocol's feed_bytes so we can track liveness at the
        # byte-stream level. The protocol layer suppresses callbacks when
        # state doesn't change (the publish_if_changed optimization), so we
        # can't rely on state-update callbacks alone — a steady-state
        # Lumagen would look unresponsive even while polls succeed.
        self._transport.set_data_callback(self._on_bytes_received)
        await self._transport.connect()
        self._started = True
        await self._send_startup_sequence()
        if (
            self._power_poll_interval is not None
            or self._status_poll_interval is not None
        ):
            self._poll_task = asyncio.create_task(
                self._poll_loop(), name="aiolumagen-poll"
            )

    async def stop(self) -> None:
        """Cancel polling and disconnect the transport. Idempotent."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._refresh_task
            self._refresh_task = None
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

        Becomes True on the first inbound bytes, reverts to False if no
        bytes have arrived within ``stale_timeout`` seconds (default 90s).
        The timeout must be longer than the longest poll interval so a
        normal poll cycle has time to write the query, await the response,
        and update ``_last_response_time`` before the next staleness check
        runs. With the default 60s polls, 90s gives one full cycle of slack
        plus another 30s for transient network delays. Tracks raw bytes
        rather than state changes so a steady-state Lumagen (polls
        succeeding but returning identical data) still counts as alive.
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

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def send_command(self, cmd: str, *, cr: bool = False, refresh: bool = True) -> None:
        """Send a raw command string.

        :param cmd: The command characters (no terminator; one is appended
            if ``cr=True``).
        :param cr: Append a carriage return. Only a handful of Lumagen
            commands need this — consult the RS-232 doc before setting it.
        :param refresh: If True (default), call :meth:`request_refresh`
            after writing the command. With Full v5 enabled (the
            documented setup) this is a cheap no-op because
            :attr:`REFRESH_TICKS` is empty by default — the device pushes
            !I25 on every state change including power. Older firmwares
            without v5 can override REFRESH_TICKS to e.g. ``(5.0,)`` to
            re-arm the post-command refresh; see the class attribute's
            docstring for context. Suppressed automatically for query
            commands (anything starting with ``Z``) so we don't recurse,
            and when no poll loop is running. Pass ``refresh=False`` to
            unconditionally suppress.
        """
        if not self._started:
            raise LumagenError("LumagenClient.send_command called before start()")
        payload = cmd.encode("ascii")
        if cr:
            payload += b"\r"
        _LOGGER.debug("TX: %s%s", cmd, "<CR>" if cr else "")
        await self._transport.write(payload)
        # Auto-refresh after control commands. The Z-prefix check skips
        # query commands (ZQS00, ZQS01, ZQS02, ZQI00, ZQI25, ZE2) so this
        # method calling itself indirectly through query_*() doesn't
        # recurse. We also skip when no poll loop is running — in that
        # configuration the caller has explicitly opted out of background
        # polling and presumably doesn't want auto-refresh either.
        if refresh and not cmd.startswith("Z") and self._poll_task is not None:
            self.request_refresh()

    async def power_on(self) -> None:
        await self.send_command(Power.ON)

    async def standby(self) -> None:
        await self.send_command(Power.STANDBY)

    async def set_input(self, n: int) -> None:
        """Select input ``n`` (1-19)."""
        await self.send_command(input_command(n))

    async def query_device_info(self) -> None:
        await self.send_command(Query.DEVICE_INFO.value)

    async def query_power(self) -> None:
        await self.send_command(Query.POWER.value)

    async def query_input_info(self) -> None:
        await self.send_command(Query.INPUT_INFO.value)

    async def query_full_status(self) -> None:
        """Send ``ZQI25`` (Full v5) — the only status poll this library issues.

        Full v5 is the supported floor. A firmware old enough not to know
        ``ZQI25`` answers with an empty payload (see the note in
        :mod:`aiolumagen.protocol` — a response prefix does not imply
        support), so on such a device every status field would simply stay
        ``None``; :meth:`_send_startup_sequence` logs a warning naming the
        requirement rather than letting that look like a wiring fault.

        The ``!I21``-``!I24`` parser branches are deliberately kept: they
        cost nothing and a device whose *reporting* menu is still set to
        Full v4 pushes ``!I24`` unsolicited even though we poll v5. Only
        the v4 *query* was removed.
        """
        await self.send_command(Query.FULL_STATUS_V5.value)

    async def query_sharpness(self) -> None:
        """Send ``ZQI30`` and update :attr:`state.sharpness_*`."""
        await self.send_command(Query.SHARPNESS.value)

    async def query_game_mode(self) -> None:
        """Send ``ZQI53`` and update :attr:`state.game_mode`."""
        await self.send_command(Query.GAME_MODE.value)

    async def query_auto_aspect(self) -> None:
        """Send ``ZQI54`` and update :attr:`state.auto_aspect`."""
        await self.send_command(Query.AUTO_ASPECT.value)

    async def query_display_rec2020(self) -> None:
        """Send ``ZQI50`` and update :attr:`state.display_supports_rec2020`."""
        await self.send_command(Query.DISPLAY_REC2020.value)

    async def query_source_hdr_status(self) -> None:
        """Send ``ZQI52`` and update :attr:`state.hdr_*` mastering fields.

        For SDR sources the Lumagen returns placeholder zeros; the parser
        leaves the structured fields at ``None`` rather than exposing
        misleading "0 nits" readings to consumers.
        """
        await self.send_command(Query.SOURCE_HDR_STATUS.value)

    async def _query_secondary_status(self) -> None:
        """Query the state the Full v5 push stream doesn't carry.

        Sharpness (``!I30``), game mode (``!I53``), auto aspect (``!I54``),
        display Rec.2020 support (``!I50``) and source HDR mastering
        metadata (``!I52``) are only emitted in response to their explicit
        ``ZQ`` queries — none of them ride in the ``!I25`` status push. If
        we never issue these, ``state.sharpness_*`` / ``game_mode`` /
        ``auto_aspect`` / ``display_supports_rec2020`` / ``hdr_*`` stay
        ``None`` forever and their HA entities read "unknown" — and any
        compound write that tries to *preserve* an unread field (e.g.
        ``set_sharpness`` keeping enabled+level while changing sensitivity)
        silently falls back to defaults.

        Run at startup and on each powered-on status poll so values changed
        from the front-panel remote stay in sync. Connection errors are
        swallowed at debug level: a blip on a secondary query must not abort
        startup or a poll cycle.
        """
        try:
            await self.query_sharpness()
            await self.query_game_mode()
            await self.query_auto_aspect()
            await self.query_display_rec2020()
            await self.query_source_hdr_status()
        except LumagenConnectionError as err:
            _LOGGER.debug("Secondary status query failed (transport down): %s", err)

    async def query_input_labels(
        self, memory: str = "A", *, settle: float = 0.15
    ) -> None:
        """Query configured labels for inputs 1-8 into :attr:`state.input_labels`.

        The Lumagen has no bulk label query, and each per-label response
        (``!S1x,<label>``) reports only the memory letter — not the input
        number. So this serializes: it primes the parser with the input it's
        about to ask for (:meth:`LumagenProtocol.expect_input_label`), sends
        the ``ZQS1XY`` query, then waits ``settle`` seconds for the reply to
        land before moving on. ``settle`` must comfortably exceed the
        round-trip time — a ~15-char reply at 9600 baud is ~15 ms, so the
        0.15 s default leaves ample slack for the ESPHome/serial path.

        :param memory: Input memory to read labels from, ``A``-``D``. Labels
            are stored per (input, memory); ``A`` is the default/primary bank.
        :param settle: Seconds to wait for each response before the next query.

        Inputs the device doesn't answer for (unpopulated inputs, or firmware
        without ``ZQS1`` support) are simply left unset — this never raises on
        a missing response, only on an invalid ``memory`` argument.
        """
        # Note: this is NOT the same vocabulary as the Memory enum. Memory
        # holds the lowercase memory-*recall* commands (``a``-``d``); the
        # label query wants an uppercase letter inside ZQS1XY. Keep them
        # separate — coercing through Memory here would send the wrong byte.
        memory = memory.upper()
        if memory not in ("A", "B", "C", "D"):
            raise LumagenCommandError(
                f"input-label memory must be 'A'-'D', got {memory!r}"
            )
        for input_number in range(1, 9):
            self._protocol.expect_input_label(input_number)
            # ZQS1XY: X = memory letter, Y = input - 1 (so '0' for input 1).
            await self.send_command(f"ZQS1{memory}{input_number - 1}")
            await asyncio.sleep(settle)

    async def set_sharpness(
        self,
        *,
        enabled: bool,
        level: int,
        sensitivity: SharpnessSensitivity = SharpnessSensitivity.NORMAL,
    ) -> None:
        """Set sharpness via ``ZY521ELS`` and re-query so state catches up.

        Sharpness is not part of the Full v5 push stream, so a follow-up
        ``ZQI30`` is needed to refresh ``state.sharpness_*`` after the
        write. We issue both back-to-back.
        """
        await self.send_command(sharpness_command(
            enabled=enabled, level=level, sensitivity=sensitivity,
        ), cr=True, refresh=False)
        await self.query_sharpness()

    async def set_game_mode(self, enabled: bool) -> None:
        """Set game mode via ``ZY551X`` and re-query ``ZQI53``."""
        await self.send_command(game_mode_command(enabled), cr=True, refresh=False)
        await self.query_game_mode()

    async def set_fan_speed(self, speed: int) -> None:
        """Set minimum fan speed via ``ZY552X`` (1-10; higher = faster).

        ``speed`` is in the device's own units — the number the Lumagen
        shows in its menu. The wire digit is one lower; see
        :func:`~aiolumagen.commands.fan_speed_command` for the evidence.

        Reverse-engineered from the firmware (see
        ``References/FIRMWARE_REVERSE_ENGINEERING_FINDINGS.md``); not
        documented in the bundled Tip0011 PDF.

        **Write-only, permanently.** There is no fan-speed query, and this
        is settled rather than merely undocumented: it's absent from
        Tip0011 and from the firmware-strings command table (the same
        extraction that *did* surface this setter and the undocumented
        ``ZQI54``), and probing ``ZQI55``-``ZQI57`` plus ``ZQS05``-``ZQS07``
        on a 4242 returned empty payloads — which, per the note in
        :mod:`aiolumagen.protocol`, is indistinguishable from a nonexistent
        code. So ``state`` will never reflect fan speed and consumers must
        track it optimistically. Don't re-probe for this.
        """
        await self.send_command(fan_speed_command(speed), cr=True, refresh=False)

    async def set_subtitle_shift(self, level: int) -> None:
        """Set subtitle shifting via ``ZY553X`` (0/1/2).

        Reverse-engineered from the firmware; not documented in the
        bundled Tip0011 PDF. Write-only — no documented query.
        """
        await self.send_command(subtitle_shift_command(level), cr=True, refresh=False)

    async def reset_auto_aspect(self) -> None:
        """``ZY550`` — reset and reinitiate automatic aspect detection.

        Re-queries ``ZQI54`` after to pick up any state change.
        """
        await self.send_command(reset_auto_aspect_command(), cr=True, refresh=False)
        await self.query_auto_aspect()

    async def set_hdr_intensity_mapping(
        self, *, display_max_nits: int, gamma_mode: HdrGammaMode = HdrGammaMode.AUTO
    ) -> None:
        """Set the active CMS's HDR mapping target via ``ZY417XXXXXG``.

        :param display_max_nits: 0 to disable HDR mapping, or 50-10000 to
            set the display's peak luminance (the target the Lumagen tone-
            maps toward).
        :param gamma_mode: :class:`~aiolumagen.state.HdrGammaMode` —
            ``AUTO`` (recommended), ``HDR`` (force HDR gamma), or ``SDR``
            (force SDR gamma).

        There's no documented query that returns the active mapping
        values, so this is fire-and-forget — the integration tracks the
        last-set values optimistically. The setting is per-CMS and
        persists until the next ``ZY417`` for the same CMS.
        """
        await self.send_command(
            hdr_intensity_mapping_command(
                display_max_nits=display_max_nits, gamma_mode=gamma_mode,
            ),
            cr=True,
            refresh=False,
        )

    def request_refresh(self) -> None:
        """Schedule follow-up status queries on the :attr:`REFRESH_TICKS` schedule.

        With Full v5 reporting enabled (the documented happy path), this
        is effectively a no-op: ``REFRESH_TICKS`` defaults to ``()`` and
        every state change — including power — pushes via ``!I25`` in
        real time. The mechanism stays in place as a safety net for
        firmwares without v5 (where ``REFRESH_TICKS`` can be set to e.g.
        ``(5.0,)`` to catch power transitions) and for future Lumagen
        quirks that may want it.

        When ticks are configured, multiple calls coalesce: an in-flight
        schedule is cancelled and restarted, so a burst of commands
        produces one window anchored on the most recent press.

        Called automatically by :meth:`send_command` for non-query
        commands when a poll loop is running.
        """
        if not self._started:
            return
        if not self.REFRESH_TICKS:
            # Empty schedule — nothing to do. Skip the task spawn entirely
            # so we don't churn through cancel/spawn cycles on every press.
            return
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(
            self._refresh_after_command(), name="aiolumagen-refresh"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _send_startup_sequence(self) -> None:
        """ZE2 echo-off, then initial queries with retry.

        Two attempts at 1.5 seconds apart is plenty of headroom — the
        RS-232 link carries no enumeration step, so once the transport is
        connected the first attempt almost always succeeds. The retry only
        matters when we attach while the ESPHome bridge is still coming up,
        which is rare in practice because ha-lumagen's coordinator setup
        waits for the esphome integration first.
        """
        max_retries = 2
        retry_interval = 1.5  # seconds between attempts

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
            if not await self._wait_for(
                lambda: self._protocol.state.model is not None, retry_interval
            ):
                if attempt < max_retries - 1:
                    _LOGGER.debug(
                        "Lumagen startup attempt %d/%d got no response, retrying",
                        attempt + 1,
                        max_retries,
                    )
                continue

            _LOGGER.debug(
                "Lumagen startup handshake succeeded on attempt %d", attempt + 1
            )
            # ZQI25 went out moments ago and its reply cannot have arrived yet
            # — !S01 is a much shorter line and beats it back every time. Give
            # the status reply its own window before judging v5 support, or the
            # check below races it and warns on a perfectly healthy device.
            got_status = await self._wait_for(
                lambda: bool(self._protocol.state.full_status_raw),
                self.FULL_STATUS_WAIT,
            )
            # The Full v5 push doesn't carry sharpness / game mode / auto
            # aspect / HDR-mapping state — pull those once now so entities
            # don't sit at "unknown" until the first poll.
            await self._query_secondary_status()
            if not got_status:
                self._warn_no_full_status()
            return

        _LOGGER.warning(
            "Lumagen startup handshake: no response after %d attempts "
            "(%.0fs total). The poll loop will continue trying.",
            max_retries,
            max_retries * retry_interval,
        )

    async def _wait_for(
        self,
        predicate: Callable[[], bool],
        timeout: float,
        *,
        interval: float = 0.1,
    ) -> bool:
        """Poll ``predicate`` until it's true or ``timeout`` elapses.

        Returns whether it came true. Deliberately a private poller rather
        than a future/event: the protocol layer is synchronous and has no
        notion of waiters, and startup is the only caller. If more callers
        appear, promote this to a public ``wait_for_state`` with real waiter
        registration instead of scattering polls.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if predicate():
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(interval)

    def _warn_no_full_status(self) -> None:
        """Warn that the device identified itself but never reported status.

        Full v5 is this library's supported floor and a firmware predating it
        doesn't fail loudly — per the note in :mod:`aiolumagen.protocol`, any
        syntactically valid ``ZQ`` code is answered by echoing the code with
        an **empty** payload. So the signal is an absent *or blank* status
        payload, not merely a missing line; the caller's predicate tests
        truthiness rather than ``is not None`` for exactly that reason.

        Only ever a log line — never promote this to an exception. Reaching
        here means ``!S01`` arrived, so the link is healthy and the device is
        talking; the integration still works, it just can't populate the
        signal-path sensors.
        """
        state = self._protocol.state
        if state.power_on is False:
            # A Lumagen in standby has no signal to report, so silence here
            # says nothing about firmware support. ZQS02 ran earlier in the
            # handshake, so power_on is known by now.
            _LOGGER.debug(
                "No status payload during startup, but the Lumagen reports "
                "standby — not treating that as missing Full v5 support"
            )
            return
        _LOGGER.warning(
            "Lumagen %s answered ZQS01 but returned no Full v5 status within "
            "%.0fs of ZQI25. This library requires firmware with Full v5 "
            "support; without it the signal sensors (resolution, aspect, "
            "colorspace, HDR) stay unknown. Updating the Lumagen's firmware "
            "is the fix.",
            state.model,
            self.FULL_STATUS_WAIT,
        )

    async def _refresh_after_command(self) -> None:
        """Fire follow-up queries at each REFRESH_TICK delay.

        Runs as a background task spawned by :meth:`request_refresh`. Each
        tick is an absolute delay from t=0 (when the task started); the
        loop sleeps the diff between the previous tick and the next. Each
        tick sends ``ZQS02`` (power) + ``ZQI25`` (full v5 status); these
        cover every state field the buttons / selects can change. Errors
        are logged at debug level but don't abort the schedule — a
        transient transport blip on one tick shouldn't suppress remaining
        ticks.
        """
        previous = 0.0
        for tick in self.REFRESH_TICKS:
            delay = max(0.0, tick - previous)
            previous = tick
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            try:
                await self.query_power()
                await self.query_full_status()
            except LumagenConnectionError as err:
                _LOGGER.debug("Refresh tick failed (transport down): %s", err)

    async def _poll_loop(self) -> None:
        """Poll at the shorter of the two intervals; gate ZQI25 on power.

        Also checks for staleness. If no response arrives within
        ``stale_timeout`` we:
          1. Mark the client as unavailable and notify listeners so the
             coordinator flags entities unavailable in HA.
          2. Force a full transport disconnect + reconnect cycle.

        The reconnect exists because a serial_proxy subscription can go
        half-open: writes still succeed and the ESP's UART keeps working,
        but inbound bytes stop reaching the existing subscriber, and the
        only recovery is a fresh subscription. Reconnecting the transport
        is functionally identical to reloading the integration from the
        ESP's perspective, without the entity churn.

        Caveat on the evidence: this was diagnosed on the retired USB/FTDI
        bridge, where a cable hot-plug reliably reproduced it. It has not
        been reproduced on the current RS-232 path, which has no
        enumeration step to lose. The recovery is cheap and idempotent so
        it stays, but treat the trigger as unverified for this hardware —
        don't cite it as established behavior for RS-232.
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
                        # Keep the non-pushed fields (sharpness, game mode,
                        # auto aspect, HDR mapping) in sync with front-panel
                        # remote changes.
                        await self._query_secondary_status()
                        s_due = now + s_iv
                except LumagenConnectionError as err:
                    _LOGGER.warning("Lumagen poll failed: %s", err)

                # Staleness check: if we were available but haven't heard
                # back in stale_timeout seconds, mark unavailable, notify,
                # and force a fresh subscription via transport reconnect.
                if self._available and not self.available:
                    self._available = False
                    _LOGGER.warning(
                        "Lumagen is now unavailable (no response in %.0fs), "
                        "forcing transport reconnect",
                        self._stale_timeout,
                    )
                    for listener in list(self._listeners):
                        with suppress(Exception):
                            listener(self._protocol.state, ("_unavailable",))
                    try:
                        await self._transport.disconnect()
                        await asyncio.sleep(1.0)
                        await self._transport.connect()
                        self._protocol.reset()
                        await self._send_startup_sequence()
                    except LumagenConnectionError as err:
                        _LOGGER.warning("Reconnect failed: %s", err)
        except asyncio.CancelledError:
            raise

    def _on_bytes_received(self, data: bytes) -> None:
        """Called for every inbound chunk — updates liveness + feeds parser.

        This is the liveness source of truth: any bytes arriving from the
        Lumagen (even a no-op poll response that doesn't change state)
        count as "the channel is healthy". The protocol layer's state-
        change callback is strictly narrower — use it for state, not
        liveness.
        """
        self._last_response_time = asyncio.get_event_loop().time()
        if not self._available:
            self._available = True
            _LOGGER.info("Lumagen is now available (first response received)")
        self._protocol.feed_bytes(data)

    def _on_protocol_update(self, state: LumagenState, codes: tuple[str, ...]) -> None:
        for listener in list(self._listeners):
            try:
                listener(state, codes)
            except Exception:
                _LOGGER.exception("Sync state listener raised; dropping it")
                with suppress(ValueError):
                    self._listeners.remove(listener)
