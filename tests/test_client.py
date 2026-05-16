"""LumagenClient integration tests against a FakeTransport."""

from __future__ import annotations

import asyncio

import pytest

from pylumagen.client import LumagenClient
from pylumagen.state import LumagenState
from tests.conftest import FakeTransport


@pytest.fixture
async def client(fake_transport: FakeTransport):
    c = LumagenClient(
        fake_transport,
        power_poll_interval=None,
        status_poll_interval=None,
    )
    yield c
    await c.stop()


async def test_start_connects_and_sends_startup_sequence(
    fake_transport: FakeTransport, client: LumagenClient
) -> None:
    await client.start()
    # Startup sequence: ZE2, then ZQS01, ZQS02, ZQI00, ZQI24.
    sent = b"".join(fake_transport.sent)
    assert b"ZE2" in sent
    assert b"ZQS01" in sent
    assert b"ZQS02" in sent
    assert b"ZQI00" in sent
    assert b"ZQI24" in sent


async def test_inbound_bytes_update_state(
    fake_transport: FakeTransport, client: LumagenClient
) -> None:
    await client.start()
    fake_transport.feed(b"!S02,1\r\n!S01,RadiancePro,030225,1018,000000\r\n")
    # The protocol is sync; state is updated inline with feed_bytes.
    assert client.state.power_on is True
    assert client.state.model == "RadiancePro"


async def test_subscribe_receives_updates(
    fake_transport: FakeTransport, client: LumagenClient
) -> None:
    received: list[tuple[LumagenState, tuple[str, ...]]] = []
    client.subscribe(lambda state, codes: received.append((state, codes)))
    await client.start()
    fake_transport.feed(b"!S02,1\r\n")
    assert received
    assert received[-1][0].power_on is True


async def test_unsubscribe_stops_updates(
    fake_transport: FakeTransport, client: LumagenClient
) -> None:
    received: list[LumagenState] = []
    unsubscribe = client.subscribe(lambda state, _codes: received.append(state))
    await client.start()
    # Clear any startup-handshake updates (e.g. auto-fed !S01)
    received.clear()
    fake_transport.feed(b"!S02,1\r\n")
    unsubscribe()
    fake_transport.feed(b"!S02,0\r\n")
    # Only the first update was seen by the listener.
    assert [s.power_on for s in received] == [True]
    # But the client's own state did update.
    assert client.state.power_on is False


async def test_commands_send_expected_bytes(
    fake_transport: FakeTransport, client: LumagenClient
) -> None:
    await client.start()
    fake_transport.sent.clear()
    await client.power_on()
    await client.standby()
    await client.set_input(3)
    assert fake_transport.sent == [b"%", b"$", b"i3"]


async def test_send_command_before_start_raises(
    fake_transport: FakeTransport,
) -> None:
    c = LumagenClient(
        fake_transport,
        power_poll_interval=None,
        status_poll_interval=None,
    )
    with pytest.raises(Exception):  # noqa: B017 — LumagenError is what we want
        await c.send_command("%")


async def test_poll_loop_fires_power_query(
    fake_transport: FakeTransport,
) -> None:
    c = LumagenClient(
        fake_transport,
        power_poll_interval=0.05,
        status_poll_interval=None,
    )
    await c.start()
    fake_transport.sent.clear()
    await asyncio.sleep(0.12)  # two poll intervals
    await c.stop()
    assert any(b"ZQS02" in chunk for chunk in fake_transport.sent)


async def test_poll_loop_skips_status_when_powered_off(
    fake_transport: FakeTransport,
) -> None:
    c = LumagenClient(
        fake_transport,
        power_poll_interval=None,
        status_poll_interval=0.05,
    )
    await c.start()
    # power_on is None (unknown) → ZQI24 is gated off
    fake_transport.sent.clear()
    await asyncio.sleep(0.12)
    await c.stop()
    assert all(b"ZQI24" not in chunk for chunk in fake_transport.sent)


async def test_liveness_tracks_bytes_not_state_changes(
    fake_transport: FakeTransport,
) -> None:
    """Steady-state polls (bytes arrive but state doesn't change) keep the
    client available. Regression for the bug where liveness was tied to
    state-change callbacks, which the parser suppresses when the Lumagen's
    values are unchanged — leading to false-positive stale detection on
    an idle but responsive device.

    Uses a long stale_timeout so the test isn't sensitive to the
    startup-sequence's internal asyncio.sleep delays.
    """
    c = LumagenClient(
        fake_transport,
        power_poll_interval=None,
        status_poll_interval=None,
        stale_timeout=10.0,
    )
    await c.start()
    # The FakeTransport auto-feeds !S01 on the startup ZQS01, so the
    # client is available after start().
    assert c.available is True
    first_response_time = c._last_response_time
    assert first_response_time is not None

    # Feed an identical !S01 — same model/firmware as before. The parser
    # will NOT fire a state-change callback because nothing changed, but
    # the byte stream should still refresh liveness.
    fake_transport.feed(b"!S01,FakeModel,000000,0000,000000\r\n")
    assert c._last_response_time is not None
    assert c._last_response_time > first_response_time
    assert c.available is True

    await c.stop()


async def test_available_goes_false_on_true_silence(
    fake_transport: FakeTransport,
) -> None:
    """When no bytes arrive at all for stale_timeout, available flips to False.

    Sets stale_timeout slightly longer than the startup sequence (~1.2s of
    asyncio.sleep) so it's reached *after* startup completes.
    """
    c = LumagenClient(
        fake_transport,
        power_poll_interval=None,
        status_poll_interval=None,
        stale_timeout=2.0,
    )
    await c.start()
    assert c.available is True

    # No feed, just wait past the stale_timeout.
    await asyncio.sleep(2.5)
    assert c.available is False

    await c.stop()


async def test_init_rejects_stale_timeout_below_poll_interval(
    fake_transport: FakeTransport,
) -> None:
    """The constructor enforces stale_timeout > longest poll interval.

    Regression for the 0.1.0 bug: stale_timeout=45s with 60s polls fired
    "no response in 45s" warnings every poll cycle even though responses
    were arriving. Root cause: the poll loop checks staleness immediately
    after sending each query, before the device's response can arrive —
    so a too-tight timeout makes elapsed-since-last-response always
    exceed it. Enforced at construction time so this can't regress.
    """
    with pytest.raises(ValueError, match="stale_timeout"):
        LumagenClient(
            fake_transport,
            power_poll_interval=60.0,
            status_poll_interval=60.0,
            stale_timeout=45.0,  # the regressing default from 0.1.0
        )


async def test_init_allows_stale_timeout_above_poll_interval(
    fake_transport: FakeTransport,
) -> None:
    """Sanity check: a sensible config is accepted."""
    c = LumagenClient(
        fake_transport,
        power_poll_interval=60.0,
        status_poll_interval=60.0,
        stale_timeout=90.0,
    )
    assert c is not None


async def test_init_allows_any_stale_timeout_when_polling_disabled(
    fake_transport: FakeTransport,
) -> None:
    """Without polling, the invariant doesn't apply (no cycle to outrun)."""
    c = LumagenClient(
        fake_transport,
        power_poll_interval=None,
        status_poll_interval=None,
        stale_timeout=2.0,
    )
    assert c is not None


async def test_send_command_schedules_refresh_after_control_command(
    fake_transport: FakeTransport,
) -> None:
    """A non-query command should trigger follow-up status queries.

    Without this, button presses take up to one full poll interval
    (~60s default) to show in HA — exactly the lag the user reported.
    """
    c = LumagenClient(
        fake_transport,
        # Long poll interval so the regular loop doesn't muddy the test.
        power_poll_interval=10.0,
        status_poll_interval=10.0,
        stale_timeout=30.0,
    )
    # Tighten the refresh ticks so the test runs fast.
    c.REFRESH_TICKS = (0.05, 0.15)
    await c.start()
    fake_transport.sent.clear()

    # Send a control command (power on).
    await c.power_on()

    # Wait past the second refresh tick.
    await asyncio.sleep(0.25)
    await c.stop()

    # We should see ZQS02 + ZQI24 fired by both refresh ticks.
    zqs02_count = sum(1 for chunk in fake_transport.sent if chunk == b"ZQS02")
    zqi24_count = sum(1 for chunk in fake_transport.sent if chunk == b"ZQI24")
    assert zqs02_count >= 2, (
        f"Expected at least 2 ZQS02 from refresh ticks, got {zqs02_count}: "
        f"{fake_transport.sent}"
    )
    assert zqi24_count >= 2, (
        f"Expected at least 2 ZQI24 from refresh ticks, got {zqi24_count}: "
        f"{fake_transport.sent}"
    )


async def test_send_command_does_not_refresh_for_query_commands(
    fake_transport: FakeTransport,
) -> None:
    """Query commands (Z-prefixed) must not trigger their own refresh — that
    would recurse into an unbounded query storm."""
    c = LumagenClient(
        fake_transport,
        power_poll_interval=10.0,
        status_poll_interval=10.0,
        stale_timeout=30.0,
    )
    c.REFRESH_TICKS = (0.05, 0.15)
    await c.start()
    fake_transport.sent.clear()

    # Sending a query directly should NOT schedule additional follow-ups.
    await c.query_power()

    await asyncio.sleep(0.25)
    await c.stop()

    # Only the explicit query should have been sent — no refresh ticks fired.
    # (We allow exactly 1 ZQS02 from the explicit call.)
    zqs02_count = sum(1 for chunk in fake_transport.sent if chunk == b"ZQS02")
    assert zqs02_count == 1, (
        f"Query command should not auto-refresh, got {zqs02_count} ZQS02s: "
        f"{fake_transport.sent}"
    )


async def test_refresh_coalesces_overlapping_calls(
    fake_transport: FakeTransport,
) -> None:
    """Bursts of commands should result in one refresh window, not N stacked."""
    c = LumagenClient(
        fake_transport,
        power_poll_interval=10.0,
        status_poll_interval=10.0,
        stale_timeout=30.0,
    )
    c.REFRESH_TICKS = (0.1, 0.3)
    await c.start()
    fake_transport.sent.clear()

    # Three commands in quick succession (e.g. user rapidly pressing buttons).
    await c.power_on()
    await c.standby()
    await c.send_command("k")  # arbitrary OSD command

    await asyncio.sleep(0.4)  # past second tick
    await c.stop()

    # Without coalescing we'd see 3 schedules x 2 ticks = 6 of each.
    # With coalescing we see exactly the most recent schedule's ticks: 2 each.
    zqs02_count = sum(1 for chunk in fake_transport.sent if chunk == b"ZQS02")
    zqi24_count = sum(1 for chunk in fake_transport.sent if chunk == b"ZQI24")
    assert zqs02_count == 2, (
        f"Expected coalesced refresh = 2 ZQS02s, got {zqs02_count}: "
        f"{fake_transport.sent}"
    )
    assert zqi24_count == 2, (
        f"Expected coalesced refresh = 2 ZQI24s, got {zqi24_count}: "
        f"{fake_transport.sent}"
    )


async def test_send_command_refresh_kwarg_can_disable(
    fake_transport: FakeTransport,
) -> None:
    """Callers that want fire-and-forget can opt out via refresh=False."""
    c = LumagenClient(
        fake_transport,
        power_poll_interval=10.0,
        status_poll_interval=10.0,
        stale_timeout=30.0,
    )
    c.REFRESH_TICKS = (0.05, 0.15)
    await c.start()
    fake_transport.sent.clear()

    await c.send_command("%", refresh=False)

    await asyncio.sleep(0.25)
    await c.stop()

    zqs02_count = sum(1 for chunk in fake_transport.sent if chunk == b"ZQS02")
    zqi24_count = sum(1 for chunk in fake_transport.sent if chunk == b"ZQI24")
    assert zqs02_count == 0, f"refresh=False should suppress ticks, got {zqs02_count}"
    assert zqi24_count == 0, f"refresh=False should suppress ticks, got {zqi24_count}"
