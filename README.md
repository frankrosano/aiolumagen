# aiolumagen

Async Python library for the Lumagen Radiance Pro RS-232 protocol.

The library is protocol-only — no Home Assistant imports, no UI, no opinion about how the state should be surfaced. The [`ha-lumagen`](../ha-lumagen) custom integration consumes it.

Transport is handled by [`serialx`](https://github.com/puddly/serialx), so aiolumagen talks to a Lumagen over whatever URL scheme serialx supports — direct USB/RS-232, raw TCP (ser2net), or an ESPHome `serial_proxy` (see [`esphome-lumagen`](../esphome-lumagen)).

> **Formerly `pylumagen`.** This project was renamed to `aiolumagen` (see [Status](#status)) to avoid a naming collision with an unrelated `pylumagen` package already published on PyPI.

## Requirements

- Python 3.14+
- `serialx >= 1.7`

### Installing for `esphome://` URLs

The ESPHome serial-proxy transport additionally needs `aioesphomeapi`, which
is **not** a direct dependency here:

```bash
pip install aiolumagen[esphome]      # standalone use
pip install aiolumagen               # inside Home Assistant
```

Home Assistant already ships `aioesphomeapi` — pinned exactly — for its own
ESPHome integration, and installs custom-integration requirements into the
same site-packages. If aiolumagen also declared it (at a necessarily looser
range), the resolver could move HA's pinned version out from under the
running ESPHome integration; because it's a Cython package, that leaves
mismatched `.so` files behind and breaks it with errors like
`APIConnection size changed` or `does not export expected C function
make_noise_packets`. So HA stays the single owner of that pin.

Nothing is lost by the split: serialx imports `aioesphomeapi` lazily per URL
scheme, so direct-serial and `socket://` URLs never touch it, and an
`esphome://` URL inside HA implies the ESPHome integration is set up — which
guarantees the package is there. Outside HA without the extra, `connect()`
raises `LumagenConnectionError` naming what to install.

## Quick start

```python
import asyncio
from aiolumagen import LumagenClient, LumagenTransport


async def main():
    transport = LumagenTransport("esphome://10.0.0.42:6053/?port_name=Lumagen&key=<base64-psk>")
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

## Lumagen-side prerequisite: enabling unsolicited reporting

For real-time status pushes (rather than just polling), configure the Lumagen to emit reports when state changes. **Full v5** pushes power transitions and memory swaps in addition to v4's coverage, so HA entities update without any post-command polling:

1. On the Lumagen remote or OSD, press `MENU`.
2. Navigate: **Other → I/O Setup → RS-232 Setup → Report mode changes**.
3. Cycle to **Full v5**.
4. Press `OK` to confirm, then `SAVE` to persist.

This writes to the Lumagen's NVRAM and persists across reboots. Without it, the library still works — it just relies on its polling loop to catch state changes.

If the reporting mode is left at **Full v4**, pushes still parse (the `!I21`–`!I24` handlers are retained) — you simply don't get power and memory changes in real time.

## Firmware requirement: Full v5

`ZQI25` (Full v5 status) is the only status query this library issues, so firmware old enough not to implement it is **not supported**. Such a device won't error: per Lumagen's protocol, any syntactically valid `ZQ` code is answered with an empty payload, so status fields would silently stay unset. The startup handshake detects this — a device that answers `ZQS01` but never `ZQI25` gets a warning naming the requirement — but the fix is a firmware update.

## Exception mapping (for Home Assistant integrators)

| Library exception | Suggested HA mapping |
|---|---|
| `LumagenConnectionError` | `ConfigEntryNotReady` from `async_setup_entry`, or mark device unavailable from the coordinator |
| `LumagenCommandError` | Log a warning; don't surface to the user. Also subclasses `ValueError`, so existing `except ValueError` handlers still catch it |

No `LumagenAuthError` — the Lumagen itself has no authentication. Any transport-layer auth errors (e.g. wrong ESPHome PSK) surface as `LumagenConnectionError` via serialx.

No timeout exception either: nothing here is request/response, so there's no outstanding request for a deadline to apply to. Impose your own with `asyncio.timeout` and catch the builtin `TimeoutError` (this is what `ha-lumagen`'s config flow does while waiting for `!S01`).

## Development

```bash
uv sync                      # install deps + dev tools
uv sync --extra esphome      # ...plus aioesphomeapi, to exercise esphome:// URLs
uv run pytest                # run tests
uv run ruff check .          # lint
uv run mypy src              # type check
```

The test suite needs no extras — it drives a fake transport, never a real
serial port. `--extra esphome` is only for pointing `examples/via_url.py` at
a live ESPHome bridge.

## Status

Alpha / prototype. Not yet published to PyPI — install from git:

```
aiolumagen @ git+https://github.com/frankrosano/pylumagen.git@main
```

## License

MIT. See [`LICENSE`](LICENSE).
