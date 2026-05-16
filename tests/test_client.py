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
