"""Talk to a Lumagen through an ESPHome serial_proxy bridge.

Usage:
    uv run python examples/via_esphome.py <host> [noise-psk]

Example:
    uv run python examples/via_esphome.py 10.105.1.42 $(grep api_encryption_key \\
        ../esphome-lumagen/secrets.yaml | cut -d\\" -f2)

The script connects, runs the startup handshake, prints any state updates
it sees for 60 s, and shuts down cleanly. Useful for confirming an
ESPHome bridge is wired correctly before wiring up the HA integration.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from pylumagen import ESPHomeTransport, LumagenClient, LumagenState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: via_esphome.py <host> [noise-psk]", file=sys.stderr)
        return 2
    host = sys.argv[1]
    psk = sys.argv[2] if len(sys.argv) > 2 else None

    transport = ESPHomeTransport(host=host, noise_psk=psk)
    client = LumagenClient(transport)

    def on_update(state: LumagenState, codes: tuple[str, ...]) -> None:
        print(f"[{','.join(codes):<8}] power={state.power_on} input={state.current_input} "
              f"resolution={state.source_resolution}@{state.source_vrate} "
              f"hdr={state.hdr_status} colorspace={state.colorspace}")

    client.subscribe(on_update)
    await client.start()
    try:
        await asyncio.sleep(60)
    finally:
        await client.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
