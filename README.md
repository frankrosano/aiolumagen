# pylumagen

Async Python library for the Lumagen Radiance Pro RS-232 protocol.

The library is protocol-only — no Home Assistant imports, no UI, no opinion about how the state should be surfaced. The [`ha-lumagen`](../ha-lumagen) custom integration consumes it.

Transport is handled by [`serialx`](https://github.com/puddly/serialx), so pylumagen talks to a Lumagen over whatever URL scheme serialx supports — direct USB/RS-232, raw TCP (ser2net), or an ESPHome `serial_proxy` (see [`esphome-lumagen`](../esphome-lumagen)).

## Requirements

- Python 3.14+
- `serialx >= 1.7`

## Quick start

```python
import asyncio
from pylumagen import LumagenClient, LumagenTransport

async def main():
    transport = LumagenTransport(
        "esphome://10.0.0.42:6053/?port_name=Lumagen&key=<base64-psk>"
    )
    client = LumagenClient(transport)

    def on_update(state, codes):
        print(state)

    client.subscribe(on_update)
    await client.start()
    await asyncio.sleep(60)
    await client.stop()

asyncio.run(main())
```

Or via the included script:

```bash
uv run python examples/via_url.py \\
    'esphome://10.105.1.42:6053/?port_name=Lumagen&key=<base64-psk>'
```

## Architecture

```
LumagenClient
├── LumagenProtocol       # line buffer + ! scan + CSV split + state model
└── LumagenTransport      # thin wrapper over serialx.create_serial_connection
```

The **protocol** is pure sync code — no I/O, easy to test with recorded bytes. The **transport** is deliberately thin: it opens a serialx URL and pipes bytes to a callback. The **client** composes the two, runs the startup handshake (`ZE2` + initial queries), and owns a background poll loop.

## State model

`LumagenState` is a slotted dataclass with typed fields for every documented status value — `power_on`, `current_input`, `source_resolution`, `is_hdr`, `colorspace` (enum: `Rec.601`/`709`/`2020`/`2100`), etc. It implements `__eq__` so an HA `DataUpdateCoordinator` can run with `always_update=False` and skip redundant writes.

Fields remain `None` until the corresponding response has been seen at least once — the Lumagen sends partial updates and the library merges them into one authoritative state snapshot.

## Lumagen-side prerequisite: enabling "Full v4" unsolicited reporting

For real-time status pushes (rather than just 60 s polling), configure the Lumagen to emit `!I24` reports when state changes:

1. On the Lumagen remote or OSD, press `MENU`.
2. Navigate: **Other → I/O Setup → RS-232 Setup → Report mode changes**.
3. Cycle to **Full v4**.
4. Press `OK` to confirm, then `SAVE` to persist.

This writes to the Lumagen's NVRAM and persists across reboots. Without this, the library still works — it just relies on its polling loop to catch state changes.

## Exception mapping (for Home Assistant integrators)

| Library exception | Suggested HA mapping |
|---|---|
| `LumagenConnectionError` | `ConfigEntryNotReady` from `async_setup_entry`, or mark device unavailable from the coordinator |
| `LumagenTimeoutError` | `UpdateFailed` from `_async_update_data` |
| `LumagenCommandError` | Log a warning; don't surface to the user |

No `LumagenAuthError` — the Lumagen itself has no authentication. Any transport-layer auth errors (e.g. wrong ESPHome PSK) surface as `LumagenConnectionError` via serialx.

## Development

```bash
uv sync                # install deps + dev tools
uv run pytest          # run tests
uv run ruff check .    # lint
uv run mypy src        # type check
```

## Status

Alpha / prototype. Not yet published to PyPI — install from git:

```
pylumagen @ git+https://github.com/frankrosano/pylumagen.git@main
```

## License

MIT. See [`LICENSE`](LICENSE).
