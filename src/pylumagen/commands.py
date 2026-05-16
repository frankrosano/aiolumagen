"""Lumagen Radiance Pro command constants and helpers.

The Lumagen speaks short ASCII commands over 9600 8N1. Most commands are a
single character with no terminator; a handful are documented to need a
carriage return. The constants in this module mirror the command tables in
``References/Tip0011_RS232CommandInterface_111023.pdf``.
"""

from __future__ import annotations

from enum import StrEnum


class Input(StrEnum):
    """Direct input-select commands (1-8 buttons).

    For inputs 9+ on models that support them, use
    :meth:`~pylumagen.client.LumagenClient.send_command` with ``iN`` where
    N is the input number.
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
    """Query commands. All expect a ``!``-prefixed response."""

    ALIVE = "ZQS00"
    DEVICE_INFO = "ZQS01"
    POWER = "ZQS02"
    INPUT_INFO = "ZQI00"
    INPUT_VIDEO = "ZQI01"
    FULL_STATUS = "ZQI24"
    FULL_STATUS_V5 = "ZQI25"
    SHARPNESS = "ZQI30"
    GAME_MODE = "ZQI53"
    AUTO_ASPECT = "ZQI54"


# Echo mode — sent once at startup to reduce command echoing and enable
# the Lumagen's "Full v4"/"Full v5" unsolicited status reports (the report
# format is set separately via the device's menu; this just turns on the
# echo-off mode that keeps responses clean). See the Lumagen RS-232 doc.
ECHO_OFF_WITH_STATUS = "ZE2"


def input_command(n: int) -> str:
    """Return the command string to select input ``n`` (1-19)."""
    if not 1 <= n <= 19:
        raise ValueError(f"input must be 1-19, got {n}")
    return f"i{n}"


def sharpness_command(*, enabled: bool, level: int, sensitivity: str = "N") -> str:
    """Build a ``ZY521ELS`` sharpness command. Requires CR terminator on send.

    :param enabled: ``True`` for ``Y`` (sharpening on), ``False`` for ``N``.
    :param level: Sharpening intensity, 0-7 (7 = strongest).
    :param sensitivity: ``"H"`` for high, ``"N"`` for normal.

    Per the Lumagen RS-232 doc (Tip0011, ``ZY521ELS<CR>``), this sets both
    horizontal and vertical sharpness to the same level. For independent
    H/V control use ``ZY522`` (not yet wrapped — call via ``send_command``).
    """
    if not 0 <= level <= 7:
        raise ValueError(f"sharpness level must be 0-7, got {level}")
    if sensitivity not in ("H", "N"):
        raise ValueError(f"sharpness sensitivity must be 'H' or 'N', got {sensitivity!r}")
    e = "Y" if enabled else "N"
    return f"ZY521{e}{level}{sensitivity}"


def game_mode_command(enabled: bool) -> str:
    """Build a ``ZY551X`` game-mode command. Requires CR terminator on send."""
    return "ZY5511" if enabled else "ZY5510"


def fan_speed_command(level: int) -> str:
    """Build a ``ZY552X`` fan-speed command. Level 0-9 (higher = faster).

    Requires CR terminator on send. Reverse-engineered from the firmware
    (see ``References/FIRMWARE_REVERSE_ENGINEERING_FINDINGS.md``); the
    bundled Tip0011 PDF doesn't document this command explicitly.
    """
    if not 0 <= level <= 9:
        raise ValueError(f"fan speed must be 0-9, got {level}")
    return f"ZY552{level}"


def subtitle_shift_command(level: int) -> str:
    """Build a ``ZY553X`` subtitle-shift command. Level 0/1/2.

    Requires CR terminator on send. Reverse-engineered from the firmware;
    the bundled Tip0011 PDF doesn't document this command explicitly.
    """
    if level not in (0, 1, 2):
        raise ValueError(f"subtitle shift must be 0, 1, or 2; got {level}")
    return f"ZY553{level}"


def reset_auto_aspect_command() -> str:
    """``ZY550`` — reset and reinitiate automatic aspect detection."""
    return "ZY550"
