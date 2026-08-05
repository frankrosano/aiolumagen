"""Lumagen Radiance Pro command constants and helpers.

The Lumagen speaks short ASCII commands over 9600 8N1. Most commands are a
single character with no terminator; a handful are documented to need a
carriage return. The constants in this module mirror the command tables in
``References/Tip0011_RS232CommandInterface_111023.pdf``.

**This module is the single source of truth for Lumagen wire commands.**
Consumers — including ``ha-lumagen``'s button/select/switch/remote tables —
must reference these members rather than re-declaring the literals. The
table is hostile to hand-transcription: 29 of the commands are a single
opaque character, and four pairs differ only by letter case while meaning
unrelated things (``v`` down vs ``V`` auto-aspect-off, ``w`` 16:9 vs ``W``
2.35, ``s`` OSD-off vs ``S`` save, ``g`` OSD-on vs ``G`` 2.40). A
mistyped literal is a valid command for a different operation and nothing
will reject it; a mistyped enum member is an ``AttributeError`` at import.

Every enum here is a :class:`~enum.StrEnum`, so members are ``str``
instances and can be handed straight to
:meth:`~aiolumagen.client.LumagenClient.send_command` without ``.value``.
"""

from __future__ import annotations

from enum import StrEnum

from aiolumagen.exceptions import LumagenCommandError
from aiolumagen.state import HdrGammaMode, SharpnessSensitivity


class Input(StrEnum):
    """Direct input-select commands (1-8 buttons).

    For inputs 9-19 on models that support them, do **not** hand-build the
    command — the encoding changes above 9 (see :func:`input_command`). Use
    :meth:`~aiolumagen.client.LumagenClient.set_input`, which routes through
    that encoder.
    """

    INPUT_1 = "i1"
    INPUT_2 = "i2"
    INPUT_3 = "i3"
    INPUT_4 = "i4"
    INPUT_5 = "i5"
    INPUT_6 = "i6"
    INPUT_7 = "i7"
    INPUT_8 = "i8"
    PREVIOUS = "P"


class Aspect(StrEnum):
    """Aspect-ratio commands."""

    RATIO_4_3 = "n"
    LETTERBOX = "l"
    RATIO_16_9 = "w"
    RATIO_16_9_NZ = "*"
    RATIO_1_85 = "j"
    RATIO_2_35 = "W"
    RATIO_2_40 = "G"
    AUTO_ENABLE = "~"
    AUTO_DISABLE = "V"


class Memory(StrEnum):
    """Memory-slot select commands."""

    A = "a"
    B = "b"
    C = "c"
    D = "d"


class Navigation(StrEnum):
    """OSD navigation commands."""

    MENU = "M"
    EXIT = "X"
    OK = "k"
    MENU_OFF = "!"
    UP = "^"
    DOWN = "v"
    LEFT = "<"
    RIGHT = ">"


class Power(StrEnum):
    """Power commands."""

    ON = "%"
    STANDBY = "$"


class Misc(StrEnum):
    """Miscellaneous single-shot commands."""

    SAVE = "S"  # Followed by OK ("k") to confirm
    HDR_SETUP = "Y"
    TEST_PATTERN = "H"
    OSD_ON = "g"
    OSD_OFF = "s"


class Query(StrEnum):
    """Query commands. All expect a ``!``-prefixed response.

    Every member here has a sending method on
    :class:`~aiolumagen.client.LumagenClient`. Don't add one speculatively —
    a ``Query`` nobody sends means a parser branch nobody can reach.
    ``ZQS00`` (alive) and ``ZQI01`` (input video format) were removed for
    exactly that reason: byte-level liveness in
    ``LumagenClient._on_bytes_received`` supersedes the former, and nothing
    ever consumed the latter.
    """

    DEVICE_INFO = "ZQS01"
    POWER = "ZQS02"
    INPUT_INFO = "ZQI00"
    # No ZQI24 (Full v4) query: v5 is the supported floor. The !I21-!I24
    # *parsers* stay, because a device whose reporting menu is still set to
    # Full v4 pushes !I24 unsolicited.
    FULL_STATUS_V5 = "ZQI25"
    SHARPNESS = "ZQI30"
    DISPLAY_REC2020 = "ZQI50"
    SOURCE_HDR_STATUS = "ZQI52"
    GAME_MODE = "ZQI53"
    AUTO_ASPECT = "ZQI54"


# Echo mode — sent once at startup to reduce command echoing and enable
# the Lumagen's "Full v4"/"Full v5" unsolicited status reports (the report
# format is set separately via the device's menu; this just turns on the
# echo-off mode that keeps responses clean). See the Lumagen RS-232 doc.
ECHO_OFF_WITH_STATUS = "ZE2"


def input_command(n: int) -> str:
    """Return the command string to select input ``n`` (1-19).

    Inputs 1-9 are ``i`` followed by the digit. **Inputs 10-19 are not
    ``i10``-``i19``** — Tip0011's command table spells this out twice:

        ``INPUT`` / ``i`` — Choose input (i.e. ``i2`` for input 2 and
        ``i+2`` for input 12)

        ``10+`` / ``+`` — Add 10 to the next digit entered for input
        selection

    So ``+`` is a prefix modifier consumed by the *next* digit, and input 12
    is ``i+2``. This function previously emitted ``f"i{n}"`` across the whole
    1-19 range, which for n>=10 sent ``i1`` (select input 1) followed by a
    stray digit the device treats as menu input. It was silently wrong — a
    valid command sequence for the wrong operation, so nothing rejected it.
    """
    if not 1 <= n <= 19:
        raise LumagenCommandError(f"input must be 1-19, got {n}")
    if n <= 9:
        return f"i{n}"
    return f"i+{n - 10}"


def sharpness_command(
    *,
    enabled: bool,
    level: int,
    sensitivity: SharpnessSensitivity = SharpnessSensitivity.NORMAL,
) -> str:
    """Build a ``ZY521ELS`` sharpness command. Requires CR terminator on send.

    :param enabled: ``True`` for ``Y`` (sharpening on), ``False`` for ``N``.
    :param level: Sharpening intensity, 0-7 (7 = strongest).
    :param sensitivity: :class:`~aiolumagen.state.SharpnessSensitivity`. A bare
        ``"H"``/``"N"`` string is still accepted and coerced, so callers that
        carry the wire letter around keep working.

    Per the Lumagen RS-232 doc (Tip0011, ``ZY521ELS<CR>``), this sets both
    horizontal and vertical sharpness to the same level. For independent
    H/V control use ``ZY522`` (not yet wrapped — call via ``send_command``).
    """
    if not 0 <= level <= 7:
        raise LumagenCommandError(f"sharpness level must be 0-7, got {level}")
    try:
        sens = SharpnessSensitivity(sensitivity)
    except ValueError:
        raise LumagenCommandError(
            f"sharpness sensitivity must be 'H' or 'N', got {sensitivity!r}"
        ) from None
    e = "Y" if enabled else "N"
    return f"ZY521{e}{level}{sens.value}"


def game_mode_command(enabled: bool) -> str:
    """Build a ``ZY551X`` game-mode command. Requires CR terminator on send."""
    return "ZY5511" if enabled else "ZY5510"


def fan_speed_command(speed: int) -> str:
    """Build a ``ZY552X`` minimum-fan-speed command. Speed 1-10.

    ``speed`` is the value the Lumagen shows in its own menu; the wire
    digit is one lower. Confirmed empirically: sending ``ZY5524`` makes
    the device report a minimum fan speed of 5, and ``ZY5523`` reports 4.
    So the documented ``X=0-9`` range (from
    ``References/FIRMWARE_REVERSE_ENGINEERING_FINDINGS.md`` — this command
    isn't in the bundled Tip0011 PDF at all) is a 0-based index into a
    1-based display. Callers work in the device's units and this encoder
    owns the conversion, so there's only one notion of "fan speed" in the
    library.

    Requires CR terminator on send.
    """
    if not 1 <= speed <= 10:
        raise LumagenCommandError(f"fan speed must be 1-10, got {speed}")
    return f"ZY552{speed - 1}"


def subtitle_shift_command(level: int) -> str:
    """Build a ``ZY553X`` subtitle-shift command. Level 0/1/2.

    Requires CR terminator on send. Reverse-engineered from the firmware;
    the bundled Tip0011 PDF doesn't document this command explicitly.
    """
    if level not in (0, 1, 2):
        raise LumagenCommandError(f"subtitle shift must be 0, 1, or 2; got {level}")
    return f"ZY553{level}"


def reset_auto_aspect_command() -> str:
    """``ZY550`` — reset and reinitiate automatic aspect detection."""
    return "ZY550"


def hdr_intensity_mapping_command(
    *, display_max_nits: int, gamma_mode: HdrGammaMode
) -> str:
    """Build a ``ZY417XXXXXG`` HDR intensity-mapping command.

    Requires a CR terminator on send. Per Tip0011:

    * ``XXXXX`` — display peak luminance in nits, zero-padded to 5 digits.
      ``00000`` disables HDR mapping; otherwise ``00050``-``10000`` sets
      the display's max level (target nits the Lumagen tone-maps toward).
    * ``G`` — gamma mode: ``A`` (auto, recommended), ``H`` (force HDR),
      ``S`` (force SDR).

    The command applies to the **currently selected output CMS**. The
    Lumagen menu reaches the same setting at
    *Output → CMS → CMSx → HDR Mapping*. There's no documented query
    that returns the current value, so callers wanting to track state
    have to remember what they last sent (the integration handles this
    optimistically).

    :param display_max_nits: 0 (disable mapping), or 50-10000 (active).
    :param gamma_mode: :class:`~aiolumagen.state.HdrGammaMode`. A bare
        ``"A"``/``"H"``/``"S"`` string is still accepted and coerced.
    """
    if display_max_nits != 0 and not 50 <= display_max_nits <= 10000:
        raise LumagenCommandError(
            f"display_max_nits must be 0 (disable) or 50-10000, got {display_max_nits}"
        )
    try:
        gamma = HdrGammaMode(gamma_mode)
    except ValueError:
        raise LumagenCommandError(
            f"gamma_mode must be 'A', 'H', or 'S'; got {gamma_mode!r}"
        ) from None
    return f"ZY417{display_max_nits:05d}{gamma.value}"
