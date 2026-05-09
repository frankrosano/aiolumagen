"""Talk to a Lumagen over a direct serial cable.

Usage:
    uv run python examples/direct_serial.py /dev/tty.usbserial-XXXX

Requires a USB-to-serial cable to the Lumagen's rear DB9 (or USB, if the
FTDI is exposed on the host). The ESPHome path in examples/via_esphome.py
is the production path — this is for dev/debug only.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from pylumagen import LumagenClient, LumagenState, SerialTransport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: direct_serial.py <device-path>", file=sys.stderr)
        return 2
    transport = SerialTransport(sys.argv[1])
    client = LumagenClient(transport)

    def on_update(state: LumagenState, codes: tuple[str, ...]) -> None:
        print(f"[{','.join(codes):<8}] power={state.power_on} input={state.current_input}")

    client.subscribe(on_update)
    await client.start()
    try:
        await asyncio.sleep(60)
    finally:
        await client.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
