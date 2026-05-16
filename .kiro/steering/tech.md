# Tech Stack

## Language & Runtime

- **Python 3.14+** (required, not "or newer than 3.10" — the lock file pins 3.14)
- Async / `asyncio` throughout the client and transport
- `from __future__ import annotations` at the top of every module

## Dependencies

Runtime:
- `serialx[esphome] >= 1.7` — universal serial transport (direct, TCP, ESPHome proxy)

Dev (in the `dev` group of `pyproject.toml`):
- `pytest >= 8`, `pytest-asyncio >= 0.24`, `pytest-cov >= 5`
- `ruff >= 0.7` for lint + format
- `mypy >= 1.11` in **strict** mode

No other runtime deps. Keep it that way unless there's a clear reason — every added dep is a dep `ha-lumagen` will inherit.

## Build / Packaging

- Build backend: `hatchling`
- Layout: `src/pylumagen/` (src layout, not flat)
- `py.typed` is shipped — the library is fully type-checked downstream
- Wheel includes `src/pylumagen`; sdist also includes `tests`, `README.md`, `LICENSE`, `pyproject.toml`

## Tooling Configuration

- **Ruff**: `line-length = 100`, `target-version = "py314"`, lint rules: `E F W I N UP B A C4 SIM RUF`
- **Mypy**: `python_version = "3.14"`, **strict = true**, `warn_unreachable = true`
- **Pytest**: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, `addopts = "-ra --strict-markers --strict-config"`

Strict mypy means every public function must be fully annotated. Don't disable strict mode — fix the types.

## Common Commands

```bash
uv sync                # install runtime + dev deps into .venv
uv run pytest          # run the test suite
uv run pytest --cov    # with coverage
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src        # type check (strict)

# Run the example against a real device
uv run python examples/via_url.py 'esphome://10.0.0.42:6053/?port_name=Lumagen&key=<base64-psk>'
```

## Versioning & Distribution

- Version lives in `pyproject.toml` and `pylumagen/__init__.py` (`__version__`). Keep them in sync.
- Not on PyPI yet — `ha-lumagen`'s manifest installs it from git (`pylumagen@git+https://github.com/frankrosano/pylumagen.git@main`).
- Local development: `ha-lumagen`'s `pyproject.toml` uses `[tool.uv.sources]` to point at this repo via path with `editable = true`.

## Lumagen Protocol Notes

- 9600 8N1 ASCII; commands 1–6 chars, no CR terminator; queries start with `ZQ`; responses start with `!`.
- The Lumagen **may echo the sent command** as a prefix on the response line. The protocol layer must scan for `!` rather than assuming it's at position 0.
- Unsolicited reports require user setup on the device: **Menu → Other → I/O Setup → RS-232 Setup → Report mode changes → Full v5 → Save** (or Full v4 on older firmware). Without this, the library still works via polling — keep that path correct.

## Exception Mapping (contract with `ha-lumagen`)

| Library exception | HA mapping (in the integration) |
|---|---|
| `LumagenConnectionError` | `ConfigEntryNotReady`, or device unavailable from the coordinator |
| `LumagenTimeoutError` | `UpdateFailed` from `_async_update_data` |
| `LumagenCommandError` | Log a warning; don't surface to the user |

There is no `LumagenAuthError` — the Lumagen has no auth. Transport-layer auth failures (e.g. wrong ESPHome PSK) surface as `LumagenConnectionError` via serialx.
