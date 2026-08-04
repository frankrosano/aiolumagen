"""Send a single raw command to a Lumagen and print everything it says back.

Useful for protocol experiments — testing whether the device understands a
new query (e.g. ``ZQI25``), checking the exact response format of a known
command, or confirming a bridge is wired correctly without committing to
the full client lifecycle.

Usage:
    uv run python examples/send_raw.py <url> <command> [--listen <seconds>]

Examples:
    # Does the device answer ZQI25? (uses the same URL form as via_url.py)
    uv run python examples/send_raw.py \\
        'esphome://10.105.1.42:6053/?port_name=Lumagen&key=<base64-psk>' \\
        ZQI25

    # Send a power-on command and watch state for 10 seconds
    uv run python examples/send_raw.py /dev/tty.usbserial-ABCD '%' --listen 10

The script:
  1. Opens the URL and runs the startup handshake (so connection issues
     surface as ``LumagenConnectionError``, same as the real integration).
  2. Sends your command exactly as written — no terminator, no auto
     refresh, no Z-prefix gating.
  3. Prints every inbound line for the listen window (default 3 s).
  4. Shuts down cleanly.

If the device responds, you'll see ``RX: <line>`` printed. If you see
nothing past the startup-handshake responses, the command was either
silently rejected or the device doesn't recognize it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from aiolumagen import LumagenClient, LumagenTransport

# DEBUG so the aiolumagen.protocol RX: lines show up — that's the whole point
# of this script. INFO would hide them.
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)-22s %(levelname)-7s %(message)s",
)
# Quiet down asyncio + serialx — we only care about aiolumagen output.
for noisy in ("asyncio", "aioesphomeapi", "serialx"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "url",
        help="serialx URL (esphome://..., /dev/tty..., socket://..., etc.)",
    )
    parser.add_argument(
        "command",
        help="Command to send, exactly as Lumagen expects (no terminator).",
    )
    parser.add_argument(
        "--listen",
        type=float,
        default=3.0,
        help="Seconds to wait for responses after sending (default: 3.0).",
    )
    parser.add_argument(
        "--cr",
        action="store_true",
        help="Append a carriage return to the command (rare; check Tip0011).",
    )
    args = parser.parse_args()

    transport = LumagenTransport(args.url)
    client = LumagenClient(transport)

    print(f"Connecting to {args.url}...", file=sys.stderr)
    await client.start()
    print("Startup handshake complete; sending command.", file=sys.stderr)
    try:
        # refresh=False so we don't trigger the post-command refresh tick —
        # this script is for raw observation, not control workflows.
        await client.send_command(args.command, cr=args.cr, refresh=False)
        print(f"Sent: {args.command!r}. Listening for {args.listen}s...", file=sys.stderr)
        await asyncio.sleep(args.listen)
    finally:
        await client.stop()
    print("Done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
