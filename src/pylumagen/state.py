"""Typed state model for a Lumagen Radiance Pro.

The state dataclass is designed to plug into Home Assistant's
``DataUpdateCoordinator`` with ``always_update=False``: two equal
``LumagenState`` instances compare equal, so unchanged polls won't trigger
entity state writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Colorspace(StrEnum):
    """Output colorspace reported in the `!I24` E field."""

    REC_601 = "Rec.601"
    REC_709 = "Rec.709"
    REC_2020 = "Rec.2020"
    REC_2100 = "Rec.2100"


class HdrStatus(StrEnum):
    """Source dynamic range reported in the `!I24` F field."""

    SDR = "SDR"
    HDR = "HDR"


class InputStatus(StrEnum):
    """Input signal status reported in the `!I24` M field (index 0)."""

    NO_SOURCE = "No Source"
    ACTIVE = "Active"
    TEST_PATTERN = "Test Pattern"


class SourceMode(StrEnum):
    """Source scan mode reported in the `!I24`/`!I25` G field (index 17).

    The Lumagen historically used ``-`` to mean "no input detected"; the
    Full v5 firmware introduced ``n`` as a distinct value for the same
    condition (observed empirically — the older ``-`` may still appear
    during transient states). Both are exposed as the same enum member
    ``NO_INPUT`` so consumers can compare by identity without caring which
    wire byte the firmware sent.
    """

    INTERLACED = "i"
    PROGRESSIVE = "p"
    NO_INPUT = "-"
    NO_INPUT_V5 = "n"


class SharpnessSensitivity(StrEnum):
    """Sharpness sensitivity reported in the ``!I30`` / ``ZY521ELS`` S field.

    ``H`` — high (more aggressive edge detection)
    ``N`` — normal
    """

    HIGH = "H"
    NORMAL = "N"


@dataclass(slots=True)
class LumagenState:
    """Snapshot of a Lumagen's reported state.

    All fields are optional — the Lumagen sends partial updates, and the
    protocol layer merges them into this single object over time. Fields
    remain ``None`` until the corresponding response has been seen at least
    once.

    Comparison (``__eq__``) is field-wise, suitable for
    ``DataUpdateCoordinator(always_update=False)``.
    """

    # --- Power / heartbeat ---
    power_on: bool | None = None
    alive: bool = False  # True after first !S00 seen

    # --- Device info (from !S01) ---
    model: str | None = None
    firmware: str | None = None
    device_info_raw: str | None = None

    # --- Input info (from !I00) ---
    current_input: str | None = None
    input_memory: str | None = None
    input_info_raw: str | None = None

    # --- Input video format (from !I01) ---
    input_video_raw: str | None = None

    # --- Full status (from !I24 / !I21-I23) ---
    input_status: InputStatus | None = None
    source_vrate: str | None = None
    source_resolution: str | None = None
    source_aspect: str | None = None
    content_aspect: str | None = None
    output_vrate: str | None = None
    output_resolution: str | None = None
    colorspace: Colorspace | None = None
    is_hdr: bool | None = None
    hdr_status: HdrStatus | None = None
    source_mode: SourceMode | None = None
    full_status_raw: str | None = None

    # --- Output mode (from !O01) ---
    output_mode_raw: str | None = None

    # --- Sharpness (from !I30 / ZY521ELS) ---
    sharpness_enabled: bool | None = None
    sharpness_level: int | None = None  # 0-7
    sharpness_sensitivity: SharpnessSensitivity | None = None
    sharpness_raw: str | None = None
    """Raw payload of the most recent !I30 — kept for diagnostic dumps."""

    # --- Game mode (from !I53 / ZY551) ---
    game_mode: bool | None = None

    # --- Auto aspect (from !I54) ---
    auto_aspect: bool | None = None

    # --- Bookkeeping ---
    last_update_codes: tuple[str, ...] = field(default_factory=tuple)
    """Codes touched by the most recent protocol update (for debugging)."""
