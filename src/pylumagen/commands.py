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


# Echo mode — sent once at startup to reduce command echoing and enable
# "Full v4" unsolicited status reports. See the Lumagen RS-232 doc.
ECHO_OFF_WITH_STATUS = "ZE2"


def input_command(n: int) -> str:
    """Return the command string to select input ``n`` (1-19)."""
    if not 1 <= n <= 19:
        raise ValueError(f"input must be 1-19, got {n}")
    return f"i{n}"
