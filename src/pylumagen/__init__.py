"""pylumagen — async Python library for the Lumagen Radiance Pro RS-232 protocol."""

from __future__ import annotations

from pylumagen.client import LumagenClient
from pylumagen.commands import Aspect, Input, Memory
from pylumagen.exceptions import (
    LumagenCommandError,
    LumagenConnectionError,
    LumagenError,
    LumagenTimeoutError,
)
from pylumagen.state import (
    Colorspace,
    HdrStatus,
    InputStatus,
    LumagenState,
    SourceMode,
)
from pylumagen.transport import LumagenTransport

__all__ = [
    "Aspect",
    "Colorspace",
    "HdrStatus",
    "Input",
    "InputStatus",
    "LumagenClient",
    "LumagenCommandError",
    "LumagenConnectionError",
    "LumagenError",
    "LumagenState",
    "LumagenTimeoutError",
    "LumagenTransport",
    "Memory",
    "SourceMode",
]

__version__ = "0.1.2"
