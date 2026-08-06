"""aiolumagen — async Python library for the Lumagen Radiance Pro RS-232 protocol."""

from __future__ import annotations

from aiolumagen.client import LumagenClient
from aiolumagen.commands import (
    INPUT_LABEL_MAX_LENGTH,
    OSD_LINE_COUNT,
    OSD_LINE_LENGTH,
    OSD_PERSIST_DURATION,
    Aspect,
    Input,
    Memory,
    Misc,
    Navigation,
    Power,
)
from aiolumagen.exceptions import (
    LumagenCommandError,
    LumagenConnectionError,
    LumagenError,
)
from aiolumagen.formatting import (
    decode_aspect_ratio,
    decode_output_mask,
    decode_vertical_rate,
    derive_horizontal_resolution,
)
from aiolumagen.state import (
    AutoAspectStatus,
    Colorspace,
    HdrGammaMode,
    HdrStatus,
    InputStatus,
    LumagenState,
    SharpnessSensitivity,
    SourceMode,
    SubtitleShift,
)
from aiolumagen.transport import LumagenTransport

__all__ = [
    # Device limits consumers need for their own input validation — exported so
    # nobody has to hardcode 10/30/2 or reach into an internal module for them.
    "INPUT_LABEL_MAX_LENGTH",
    "OSD_LINE_COUNT",
    "OSD_LINE_LENGTH",
    "OSD_PERSIST_DURATION",
    "Aspect",
    "AutoAspectStatus",
    "Colorspace",
    "HdrGammaMode",
    "HdrStatus",
    "Input",
    "InputStatus",
    "LumagenClient",
    "LumagenCommandError",
    "LumagenConnectionError",
    "LumagenError",
    "LumagenState",
    "LumagenTransport",
    "Memory",
    "Misc",
    "Navigation",
    "Power",
    "SharpnessSensitivity",
    "SourceMode",
    "SubtitleShift",
    "decode_aspect_ratio",
    "decode_output_mask",
    "decode_vertical_rate",
    "derive_horizontal_resolution",
]

__version__ = "0.7.0"
