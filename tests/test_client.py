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
        startup_delay=0.0,
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
        startup_delay=0.0,
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
        startup_delay=0.0,
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
        startup_delay=0.0,
        power_poll_interval=None,
        status_poll_interval=0.05,
    )
    await c.start()
    # power_on is None (unknown) → ZQI24 is gated off
    fake_transport.sent.clear()
    await asyncio.sleep(0.12)
    await c.stop()
    assert all(b"ZQI24" not in chunk for chunk in fake_transport.sent)
