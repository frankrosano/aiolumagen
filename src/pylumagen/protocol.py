"""Lumagen response parser.

Direct port of the C++ ``lumagen_parser`` that used to live in
``esphome-lumagen/components/``. The logic is deliberately mechanical: the
Lumagen protocol is stable and its field layouts are documented, so there's
no value in cleverness here — just a line buffer, a ``!``-scan, a CSV split,
and a small dispatch table.

Key invariants (from the old C++ comments, confirmed against captures):

* The Lumagen may echo the query command on the same line as the response,
  e.g. ``ZQS01!S01,RadiancePro,...``. Always locate the response by scanning
  for ``!``, not by assuming position 0.
* Lines terminate with ``\\r\\n``, but some firmware revisions send only
  ``\\n``. Strip whichever we see.
* Some lines arrive without a final newline before the next line's prefix
  (rare; happens when the Lumagen coalesces unsolicited reports). We don't
  try to handle that — we trust the boundary is a newline.
* Known response codes: S00, S01, S02, I00, I01, I21, I22, I23, I24, I25,
  O01. Unknown codes are logged at DEBUG and ignored.

Full v5 (``ZQI25`` / ``!I25``) is the recommended unsolicited-reporting
mode for current Lumagen firmware. It extends the v4 layout with two new
fields at the end:

* index 23 — active input memory letter (``A``/``B``/``C``/``D``)
* index 24 — power state (``0``/``1``)

These were previously only obtainable via separate ``ZQI00`` and ``ZQS02``
queries; v5 pushes them on every state change so power transitions and
memory swaps reach listeners in real time without a follow-up poll.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from pylumagen.state import (
    Colorspace,
    HdrStatus,
    InputStatus,
    LumagenState,
    SharpnessSensitivity,
    SourceMode,
)

_LOGGER = logging.getLogger(__name__)

MAX_RX_BUFFER = 4096
"""Maximum in-flight line length before we give up and discard.

The C++ implementation used 512 for the tight ESP32 memory budget; 4096 is
fine in Python and gives us plenty of headroom for pathological bursts.
"""


# I24 field indices. Documented in the old C++ header and in
# Tip0011_RS232CommandInterface. The comment keys map to Lumagen's
# single-letter field names.
_I24_INPUT_STATUS = 0  # M
_I24_SOURCE_VRATE = 1  # RRR
_I24_SOURCE_RESOLUTION = 2  # VVVV
_I24_SOURCE_ASPECT = 5  # AAA
_I24_CONTENT_ASPECT = 6  # SSS
_I24_OUTPUT_VRATE = 12  # PPP
_I24_OUTPUT_RESOLUTION = 13  # QQQQ
_I24_COLORSPACE = 15  # E: 0=601, 1=709, 2=2020, 3=2100
_I24_HDR_FLAG = 16  # F: 0=SDR, 1=HDR
_I24_SOURCE_MODE = 17  # G: i, p, -, n
_I24_VIRTUAL_INPUT = 19  # II: 1-19

# I25-only fields appended to the v4 layout (see module docstring).
_I25_INPUT_MEMORY = 23  # A/B/C/D
_I25_POWER_STATE = 24  # 0=off, 1=on

_INPUT_STATUS_MAP = {
    "0": InputStatus.NO_SOURCE,
    "1": InputStatus.ACTIVE,
    "2": InputStatus.TEST_PATTERN,
}

_COLORSPACE_MAP = {
    "0": Colorspace.REC_601,
    "1": Colorspace.REC_709,
    "2": Colorspace.REC_2020,
    "3": Colorspace.REC_2100,
}


# Type alias for the per-update callback handed to LumagenProtocol.
StateUpdateCallback = Callable[[LumagenState, tuple[str, ...]], None]
"""Callback shape: ``(new_state, codes_touched) -> None``.

``codes_touched`` lists the 3-char response codes that contributed to this
update — e.g. ``("I24",)`` for a full-status report, ``("S02",)`` for a
power-only change.
"""


class LumagenProtocol:
    """Streaming parser for Lumagen responses.

    Feed bytes in with :meth:`feed_bytes`. Complete lines trigger a state
    update and the supplied callback is invoked with the new state and the
    set of codes that were touched. The protocol is fully synchronous — it
    does no I/O — so it's easy to test in isolation.
    """

    def __init__(self, on_update: StateUpdateCallback) -> None:
        self._on_update = on_update
        self._state = LumagenState()
        self._rx = bytearray()

    @property
    def state(self) -> LumagenState:
        """Current accumulated state snapshot."""
        return self._state

    def reset(self) -> None:
        """Discard any partial line. Call on reconnect."""
        self._rx.clear()

    def feed_bytes(self, data: bytes | bytearray) -> None:
        """Append inbound bytes and emit an update for each complete line.

        A ``line`` ends at the first ``\\n`` we see. Trailing ``\\r`` is
        stripped. Empty lines are ignored. If the buffer exceeds
        :data:`MAX_RX_BUFFER` without a newline, we discard and warn — this
        is recovery from a missing terminator, not normal operation.
        """
        if not data:
            return
        self._rx.extend(data)
        while True:
            nl = self._rx.find(b"\n")
            if nl < 0:
                if len(self._rx) > MAX_RX_BUFFER:
                    _LOGGER.warning(
                        "RX buffer exceeded %d bytes without a newline; discarding", MAX_RX_BUFFER
                    )
                    self._rx.clear()
                return
            raw = bytes(self._rx[:nl])
            del self._rx[: nl + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if not raw:
                continue
            try:
                line = raw.decode("ascii", errors="replace")
            except Exception:  # pragma: no cover — decode("replace") can't raise
                continue
            self._process_line(line)

    # ------------------------------------------------------------------
    # Line / response handling
    # ------------------------------------------------------------------

    def _process_line(self, line: str) -> None:
        _LOGGER.debug("RX: %s", line)
        bang = line.find("!")
        if bang < 0:
            # No response marker — probably an echo of our own command.
            return
        tail = line[bang + 1 :]
        if len(tail) < 3:
            return
        code = tail[:3]
        data = tail[4:] if len(tail) > 3 and tail[3] == "," else ""
        self._handle_response(code, data)

    def _handle_response(self, code: str, data: str) -> None:
        # Build a shallow copy of the current state, mutate fields on it,
        # and swap it in at the end. This avoids ``replace(**kwargs)`` with
        # a loosely-typed dict (mypy rightly rejects that) while still
        # firing exactly one callback per inbound line.
        pending = replace(self._state)
        touched: tuple[str, ...] = (code,)

        if code == "S00":
            pending.alive = True
        elif code == "S01":
            self._handle_s01(data, pending)
        elif code == "S02":
            pending.power_on = data[:1] == "1"
        elif code == "I00":
            self._handle_i00(data, pending)
        elif code == "I01":
            pending.input_video_raw = data
        elif code == "I24":
            pending.full_status_raw = data
            self._apply_i24(data, pending)
        elif code == "I25":
            pending.full_status_raw = data
            self._apply_i25(data, pending)
        elif code in ("I21", "I22", "I23"):
            pending.full_status_raw = data
            self._apply_i2x(data, pending)
        elif code == "I30":
            self._handle_i30(data, pending)
        elif code == "I50":
            pending.display_supports_rec2020 = data[:1] == "Y"
        elif code == "I52":
            self._handle_i52(data, pending)
        elif code == "I53":
            pending.game_mode = data[:1] == "1"
        elif code == "I54":
            pending.auto_aspect = data[:1] == "1"
        elif code == "O01":
            pending.output_mode_raw = data
        else:
            _LOGGER.debug("Unhandled Lumagen code !%s,%s", code, data)
            return

        pending.last_update_codes = touched
        # Compare ignoring last_update_codes so that "no payload change"
        # polls don't wake up listeners.
        if self._state_matches_ignoring_codes(pending):
            self._state = pending
            return
        self._state = pending
        self._on_update(self._state, touched)

    def _state_matches_ignoring_codes(self, pending: LumagenState) -> bool:
        """Return True if ``pending`` equals current state but for the codes tuple."""
        cur = self._state
        if cur is pending:
            return True
        current_codes = cur.last_update_codes
        pending_codes = pending.last_update_codes
        cur.last_update_codes = pending_codes
        try:
            return cur == pending
        finally:
            cur.last_update_codes = current_codes

    # ------------------------------------------------------------------
    # Per-code handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_s01(data: str, state: LumagenState) -> None:
        """!S01 = ``<model>,<firmware>,<serial>``."""
        state.device_info_raw = data
        parts = data.split(",", 2)
        if len(parts) >= 1:
            state.model = parts[0]
        if len(parts) >= 2:
            state.firmware = parts[1]

    @staticmethod
    def _handle_i00(data: str, state: LumagenState) -> None:
        """!I00 = ``<input>,<memory>,<config>``."""
        state.input_info_raw = data
        parts = data.split(",", 2)
        if len(parts) >= 1:
            state.current_input = parts[0]
        if len(parts) >= 2:
            state.input_memory = parts[1]

    @staticmethod
    def _handle_i30(data: str, state: LumagenState) -> None:
        """!I30 = sharpness setting, format ``<E><L><S>`` matching ZY521ELS.

        E = ``Y``/``N`` (enabled), L = ``0``-``7`` (level), S = ``H``/``N``
        (sensitivity). The Lumagen doc (Tip0011, ``ZQI30``) describes the
        response only as "Returns values corresponding to the YZ521ELS
        command" — no published example payload, so this implementation
        is best-effort. Always stash the raw payload so a diagnostic dump
        can show the wire bytes if the structured fields look wrong.
        """
        state.sharpness_raw = data
        if len(data) >= 1 and data[0] in ("Y", "N"):
            state.sharpness_enabled = data[0] == "Y"
        if len(data) >= 2 and data[1].isdigit():
            level = int(data[1])
            if 0 <= level <= 7:
                state.sharpness_level = level
        if len(data) >= 3 and data[2] in ("H", "N"):
            state.sharpness_sensitivity = SharpnessSensitivity(data[2])

    @staticmethod
    def _handle_i52(data: str, state: LumagenState) -> None:
        """!I52 = source HDR status: ``V,Min,Max,Cll``.

        Per Tip0011:

        * ``V`` = 0 (source not HDR) or 1 (source is HDR)
        * ``Min`` = source mastering display minimum luminance, decimal
          (e.g. ``.0050``).
        * ``Max`` = source mastering display max luminance, integer nits.
        * ``Cll`` = MaxCLL, integer nits.

        For SDR sources the device fills Min/Max/Cll with zero
        placeholders (``0,.0000,0,0``). We only populate the structured
        fields when V=1; SDR keeps them at None so an entity reads
        "unknown" rather than a misleading "0 nits".
        """
        parts = data.split(",")
        if not parts:
            return
        is_hdr = parts[0] == "1"
        # Note: !I52 is parallel to !I24's HDR flag; trust !I24 as primary
        # but also reflect this query's view in is_hdr/hdr_status so a
        # standalone ZQI52 still updates the sensors.
        state.is_hdr = is_hdr
        state.hdr_status = HdrStatus.HDR if is_hdr else HdrStatus.SDR
        if not is_hdr:
            # Clear any stale mastering metadata when the source flips to SDR.
            state.hdr_source_min_luminance = None
            state.hdr_source_max_luminance = None
            state.hdr_source_max_cll = None
            return

        # HDR source — pull mastering metadata.
        if len(parts) >= 2:
            try:
                state.hdr_source_min_luminance = float(parts[1])
            except (TypeError, ValueError):
                _LOGGER.debug("Could not parse !I52 Min field %r", parts[1])
        if len(parts) >= 3:
            try:
                state.hdr_source_max_luminance = int(parts[2])
            except (TypeError, ValueError):
                _LOGGER.debug("Could not parse !I52 Max field %r", parts[2])
        if len(parts) >= 4:
            try:
                state.hdr_source_max_cll = int(parts[3])
            except (TypeError, ValueError):
                _LOGGER.debug("Could not parse !I52 Cll field %r", parts[3])

    @staticmethod
    def _apply_i24(data: str, state: LumagenState) -> None:
        """!I24 = Full v4 status, 23 comma-separated fields."""
        fields = data.split(",")
        LumagenProtocol._apply_i2x_common(fields, state)

        # I24-only fields (colorspace, HDR, source mode, virtual input)
        if len(fields) > _I24_COLORSPACE:
            cs = _COLORSPACE_MAP.get(fields[_I24_COLORSPACE])
            if cs is not None:
                state.colorspace = cs
        if len(fields) > _I24_HDR_FLAG:
            is_hdr = fields[_I24_HDR_FLAG] == "1"
            state.is_hdr = is_hdr
            state.hdr_status = HdrStatus.HDR if is_hdr else HdrStatus.SDR
        if len(fields) > _I24_SOURCE_MODE:
            raw = fields[_I24_SOURCE_MODE]
            try:
                state.source_mode = SourceMode(raw)
            except ValueError:
                _LOGGER.debug("Unknown source mode %r", raw)
        if len(fields) > _I24_VIRTUAL_INPUT:
            state.current_input = fields[_I24_VIRTUAL_INPUT]

    @staticmethod
    def _apply_i25(data: str, state: LumagenState) -> None:
        """!I25 = Full v5 status, v4 layout + 2 trailing fields.

        Reuses :meth:`_apply_i24` for the 23 v4-shared fields, then pulls
        the v5 additions: active input memory letter (idx 23) and power
        state (idx 24). With v5 enabled on the device, power transitions
        and memory swaps reach listeners as part of the unsolicited push
        stream, eliminating the need for a follow-up ZQS02/ZQI00 poll
        after every control command.
        """
        LumagenProtocol._apply_i24(data, state)

        fields = data.split(",")
        if len(fields) > _I25_INPUT_MEMORY:
            mem = fields[_I25_INPUT_MEMORY]
            if mem:  # Treat empty string the same as "not reported"
                state.input_memory = mem
        if len(fields) > _I25_POWER_STATE:
            state.power_on = fields[_I25_POWER_STATE] == "1"

    @staticmethod
    def _apply_i2x(data: str, state: LumagenState) -> None:
        """!I21/I22/I23 = older unsolicited formats (leading fields only)."""
        LumagenProtocol._apply_i2x_common(data.split(","), state)

    @staticmethod
    def _apply_i2x_common(fields: list[str], state: LumagenState) -> None:
        """Fields shared by I21/I22/I23/I24 - input status through output res."""
        if len(fields) > _I24_INPUT_STATUS:
            status = _INPUT_STATUS_MAP.get(fields[_I24_INPUT_STATUS])
            if status is not None:
                state.input_status = status
        if len(fields) > _I24_SOURCE_VRATE:
            state.source_vrate = fields[_I24_SOURCE_VRATE]
        if len(fields) > _I24_SOURCE_RESOLUTION:
            state.source_resolution = fields[_I24_SOURCE_RESOLUTION]
        if len(fields) > _I24_SOURCE_ASPECT:
            state.source_aspect = fields[_I24_SOURCE_ASPECT]
        if len(fields) > _I24_CONTENT_ASPECT:
            state.content_aspect = fields[_I24_CONTENT_ASPECT]
        if len(fields) > _I24_OUTPUT_VRATE:
            state.output_vrate = fields[_I24_OUTPUT_VRATE]
        if len(fields) > _I24_OUTPUT_RESOLUTION:
            state.output_resolution = fields[_I24_OUTPUT_RESOLUTION]
