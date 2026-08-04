"""Connect to a Lumagen at any serialx-supported URL.

Usage:
    uv run python examples/via_url.py <url>

Examples:
    # ESPHome bridge (PSK in URL query string)
    uv run python examples/via_url.py \\
        'esphome://10.105.1.42:6053/?port_name=Lumagen&key=<base64-psk>'

    # Direct serial
    uv run python examples/via_url.py /dev/tty.usbserial-ABCD

    # Raw TCP / ser2net
    uv run python examples/via_url.py socket://10.0.0.42:5555

The script opens the URL, runs the startup handshake, prints any state
updates for 60 s, and shuts down cleanly. Useful for confirming a bridge
is wired correctly before hooking up the HA integration.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiolumagen import LumagenClient, LumagenState, LumagenTransport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <url>", file=sys.stderr)
        return 2

    transport = LumagenTransport(sys.argv[1])
    client = LumagenClient(transport)

    start = asyncio.get_running_loop().time()

    def on_update(state: LumagenState, codes: tuple[str, ...]) -> None:
        elapsed = asyncio.get_running_loop().time() - start
        print(
            f"[{elapsed:6.3f}s] [{','.join(codes):<8}] "
            f"power={state.power_on} input={state.current_input} "
            f"resolution={state.source_resolution}@{state.source_vrate} "
            f"hdr={state.hdr_status} colorspace={state.colorspace}"
        )

    client.subscribe(on_update)
    await client.start()
    try:
        await asyncio.sleep(60)
    finally:
        await client.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
