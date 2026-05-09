# pylumagen

Async Python library for the Lumagen Radiance Pro RS-232 protocol, with two interchangeable transports:

- **ESPHome serial-proxy** — production path. Connects through an ESPHome bridge (see [`esphome-lumagen`](../esphome-lumagen)) via `aioesphomeapi`.
- **Direct serial** — development path. Talks to a Lumagen over a local `/dev/tty.*` device via `pyserial-asyncio`.

The library is protocol-only — no Home Assistant imports, no UI, no opinion about how the state should be surfaced. The [`ha-lumagen`](../ha-lumagen) custom integration consumes it.

## Requirements

- Python 3.14+
- `aioesphomeapi >= 44`
- `pyserial-asyncio >= 0.6`
- For the ESPHome transport: the Lumagen on the far end of an ESPHome device running [`esphome-lumagen`](../esphome-lumagen) firmware with `serial_proxy` enabled (ESPHome 2026.3+).

## Quick start

```python
import asyncio
from pylumagen import ESPHomeTransport, LumagenClient

async def main():
    transport = ESPHomeTransport(host="10.0.0.42", noise_psk="...")
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
uv run python examples/via_esphome.py 10.0.0.42 <noise-psk>
```

## Architecture

```
LumagenClient
├── LumagenProtocol        # line buffer + ! scan + CSV split + state model
└── LumagenTransport       # abstract bidirectional byte pipe
    ├── ESPHomeTransport   # aioesphomeapi serial_proxy
    └── SerialTransport    # pyserial-asyncio
```

- The **protocol** is pure sync code — it does no I/O and is easy to test with recorded bytes.
- The **transport** shuttles bytes; it knows nothing about the Lumagen protocol.
- The **client** composes the two, runs the startup handshake (`ZE2` + initial queries), owns a background poll loop, and exposes a callback-based subscription API.

## State model

`LumagenState` is a slotted dataclass with typed fields for every documented status value — `power_on`, `current_input`, `source_resolution`, `is_hdr`, `colorspace` (enum: `Rec.601`/`709`/`2020`/`2100`), etc. It implements `__eq__` so the HA `DataUpdateCoordinator` can run with `always_update=False` and skip redundant writes.

Fields remain `None` until the corresponding response has been seen at least once — the Lumagen sends partial updates and the library merges them into one authoritative state snapshot.

## Lumagen-side prerequisite: enabling "Full v4" unsolicited reporting

For real-time status pushes (rather than just 60 s polling), configure the Lumagen to emit `!I24` reports when state changes:

1. On the Lumagen remote or OSD, press `MENU`.
2. Navigate: **Other → I/O Setup → RS-232 Setup → Report mode changes**.
3. Cycle to **Full v4**.
4. Press `OK` to confirm, then `SAVE` to persist.

This writes to the Lumagen's NVRAM and persists across reboots. Without this, the library still works — it just relies on its polling loop to catch state changes.

(The old firmware had a scripted OSD-walk to do this automatically. That logic was intentionally dropped in favor of one-time manual setup because the walk was fragile and assumed the menu began at defaults.)

## Exception mapping (for Home Assistant integrators)

| Library exception | Suggested HA mapping |
|---|---|
| `LumagenConnectionError` | `ConfigEntryNotReady` from `async_setup_entry`, or mark device unavailable from the coordinator |
| `LumagenTimeoutError` | `UpdateFailed` from `_async_update_data` |
| `LumagenCommandError` | Log a warning; don't surface to the user |

No `LumagenAuthError` — the Lumagen itself has no authentication. Authentication errors from the ESPHome transport surface as `aioesphomeapi.APIConnectionError`, which the integration should handle separately at the config-entry layer.

## Development

```bash
uv sync                # install deps + dev tools
uv run pytest          # run tests
uv run ruff check .    # lint
uv run mypy src        # type check
```

## Status

Alpha. `v0.1.0` is an initial scaffold. Not yet published to PyPI — install from git during development:

```
pylumagen @ git+https://github.com/frankrosano/pylumagen.git@main
```

## License

MIT. See [`LICENSE`](LICENSE).
