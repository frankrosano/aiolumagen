# Tech Stack

## Language & Runtime

- **Python 3.14+** (required, not "or newer than 3.10" — the lock file pins 3.14)
- Async / `asyncio` throughout the client and transport
- `from __future__ import annotations` at the top of every module

## Dependencies

Runtime:
- `serialx >= 1.7` — universal serial transport (direct, TCP, ESPHome proxy)

**Do not add the `[esphome]` extra to the base dependency.** It only pulls
`aioesphomeapi`, which Home Assistant pins *exactly* for its own esphome
integration and installs into the same site-packages. Any range we declare
lets the resolver move HA's pin; because aioesphomeapi is Cython, that
rewrite leaves mismatched `.so` files and kills the ESPHome integration
("APIConnection size changed", "does not export expected C function
make_noise_packets").

Omitting the extra costs nothing. serialx imports the platform lazily by URL
scheme, and in HA it's guaranteed present regardless: the `usb` integration
imports `serialx.platforms.serial_esphome` at module scope via
`serial_proxy_stub`, so any install that brings up `usb` — which
`ha-lumagen` requires — has already loaded it. Standalone users opt in with
`pip install aiolumagen[esphome]`.

### HA's pins are moving targets — don't hard-code them

Every version below is a snapshot, not a constant. HA bumps these with most
releases, so treat any number written down here as stale and read the live
value from `homeassistant/components/<domain>/manifest.json` in the target
install.

| | HA 2026.5.1 | HA 2026.7.4 |
|---|---|---|
| `aioesphomeapi` | `==44.21.0` | `==45.3.1` |
| `serialx` | `==1.7.1` | `==1.8.2` |

A whole major version of `aioesphomeapi` inside two minor HA releases. This
is the argument against "just match HA's pin": whatever we wrote would be
wrong by the next release, which is why the dependency is dropped entirely
rather than pinned.

#### `serialx` is a different case — the floor is guaranteed

serialx has the same superficial shape (HA pins it exactly, and it ships a
compiled extension, `_serialx_rust.abi3.so`), and unlike `aioesphomeapi` it
can't be dropped — it's the transport. But the exposure is narrower than it
looks, and the safe half is by design rather than coincidence:

- **Floor — guaranteed.** `ha-lumagen`'s `hacs.json` sets a minimum HA of
  `2026.5.0`, and that release pins `serialx==1.7.1`. So every supported HA
  already ships a serialx satisfying our `>=1.7`, and pip never has to
  *upgrade* serialx on our account. Raising the HACS minimum can only raise
  HA's serialx, never lower it — so this can't regress.
- **Ceiling — unbounded, currently inert.** `>=1.7` has no upper bound, so
  nothing in our metadata prevents a resolver installing *above* HA's exact
  pin. Today nothing can: `1.8.2` is simultaneously HA 2026.7.4's pin and the
  newest release on PyPI, so there is nothing higher to install. That half is
  circumstantial and changes the day serialx 1.9 ships before HA adopts it.

Leaving it unbounded is deliberate. Capping to `<2` buys nothing (it still
permits 1.9 over 1.8.2), and pinning exactly would recreate the same drift
problem that made us drop `aioesphomeapi` in the first place. The residual
window — a serialx release HA hasn't picked up yet — is small, since HA
tracks it closely (2026.5 → 1.7.1, 2026.7 → 1.8.2).

Dev (in the `dev` group of `pyproject.toml`):
- `pytest >= 8`, `pytest-asyncio >= 0.24`, `pytest-cov >= 5`
- `ruff >= 0.7` for lint + format
- `mypy >= 1.11` in **strict** mode

### `uv.lock` is deliberately not tracked

Both repos gitignore it. The dev environment is *meant* to float: this stack
is developed against whatever HA is current, because that's what the author
runs in production. A test env that tracks HA's latest surfaces HA
regressions at the same time they'd bite live, which is the point.

The tradeoff is accepted, not overlooked: a green run isn't reproducible
months later, and a CI failure may come from an HA bump rather than a code
change. Don't "fix" this by committing a lock or pinning
`pytest-homeassistant-custom-component`.

No other runtime deps. Keep it that way unless there's a clear reason — every added dep is a dep `ha-lumagen` will inherit.

## Build / Packaging

- Build backend: `hatchling`
- Layout: `src/aiolumagen/` (src layout, not flat)
- `py.typed` is shipped — the library is fully type-checked downstream
- Wheel includes `src/aiolumagen`; sdist also includes `tests`, `README.md`, `LICENSE`, `pyproject.toml`

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

- Version lives in `pyproject.toml` and `aiolumagen/__init__.py` (`__version__`). Keep them in sync.
- Not on PyPI yet — `ha-lumagen`'s manifest installs it from git (`aiolumagen@git+https://github.com/frankrosano/aiolumagen.git@<tag>`). Pin the tag you cut rather than `@main` so an install resolves to a known artifact.
- Local development: `ha-lumagen`'s `pyproject.toml` uses `[tool.uv.sources]` to point at this repo via path with `editable = true`.

## Lumagen Protocol Notes

- 9600 8N1 ASCII; commands 1–6 chars, no CR terminator; queries start with `ZQ`; responses start with `!`.
- The Lumagen **may echo the sent command** as a prefix on the response line. The protocol layer must scan for `!` rather than assuming it's at position 0.
- Unsolicited reports require user setup on the device: **Menu → Other → I/O Setup → RS-232 Setup → Report mode changes → Full v5 → Save**. Without this, the library still works via polling — keep that path correct. A device left on Full v4 also works: the `!I21`–`!I24` parsers are retained, so pushes still land, minus power/memory in real time.
- **Full v5 is the supported firmware floor.** `ZQI25` is the only status query issued; there is deliberately no `ZQI24` query. Don't add one back — if pre-v5 support is ever needed, make it an explicit client option rather than a probe, because the device answers any valid `ZQ` code with an empty payload so "is v5 supported" can't be reliably detected at runtime.

## Exception Mapping (contract with `ha-lumagen`)

| Library exception | HA mapping (in the integration) |
|---|---|
| `LumagenConnectionError` | `ConfigEntryNotReady`, or device unavailable from the coordinator |
| `LumagenCommandError` | Log a warning; don't surface to the user. Also subclasses `ValueError` |

There is no `LumagenAuthError` — the Lumagen has no auth. Transport-layer auth failures (e.g. wrong ESPHome PSK) surface as `LumagenConnectionError` via serialx.

There is no timeout exception either. Nothing in the library is request/response, so there's no outstanding request a deadline could apply to; consumers impose their own with `asyncio.timeout` and catch the builtin `TimeoutError`. Don't re-add one unless the library grows a real `wait_for_state` waiter.
