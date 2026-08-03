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


class HdrGammaMode(StrEnum):
    """Gamma-mode flag in ``ZY417XXXXXG`` HDR intensity-mapping commands.

    Per Tip0011: the trailing ``G`` selects which gamma curve feeds the
    3D LUT during HDR-to-SDR mapping.

    * ``A`` — auto (Lumagen picks based on source metadata, recommended)
    * ``H`` — force HDR gamma
    * ``S`` — force SDR gamma

    There's no documented query for the active mode, so this enum only
    serves the write side — the integration tracks the last-set value
    optimistically.
    """

    AUTO = "A"
    HDR = "H"
    SDR = "S"


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
    """Model *name*, e.g. ``RadiancePro``. Identical across the product line."""

    firmware: str | None = None
    """Software revision as an ``MMDDYY`` date code, e.g. ``030225``."""

    model_number: str | None = None
    """Manufacturer's model *number*, e.g. ``1018``.

    Unlike :attr:`model` this distinguishes hardware within the line —
    Tip0011 gives Radiance XD as 1009 and XE as 1010. There's no published
    number-to-name table beyond those, so treat it as an opaque identifier.
    """

    serial: str | None = None
    """Serial number as reported by ``!S01``, verbatim.

    **Not reliable as a unique identifier.** Some units report all zeros
    (``000000``); the value is passed through unchanged rather than
    normalised to ``None`` so consumers can tell "device said zeros" from
    "not yet observed". Check for a non-zero value before using it as
    identity.
    """

    device_info_raw: str | None = None
    """Whole ``!S01`` payload — kept so an unexpected field layout stays
    diagnosable even though the fields above are parsed out of it."""

    # --- Input info (from !I00) ---
    current_input: str | None = None
    input_memory: str | None = None
    input_info_raw: str | None = None

    # --- Input labels (from !S1x, populated by ZQS1XY queries) ---
    input_labels: dict[int, str] = field(default_factory=dict)
    """Logical input number (1-8) -> configured label (e.g. ``{2: "Apple TV"}``).

    Empty until :meth:`~pylumagen.client.LumagenClient.query_input_labels`
    runs. The Lumagen's label response (``!S1x,<label>``) carries only the
    *memory* letter, not the input number, so the protocol layer correlates
    each response to the input the client last asked about (see
    ``LumagenProtocol.expect_input_label``). Labels are per (input, memory);
    this map holds one memory's worth (memory A by default).
    """

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

    # --- HDR — source mastering metadata (from !I52) ---
    # Populated only when V=1 (source is actually HDR). For SDR sources
    # the device reports placeholder zeros; we leave fields as None
    # rather than expose those as "0 nits" which would be misleading.
    hdr_source_min_luminance: float | None = None
    """Source mastering display minimum luminance in nits (e.g. 0.0050)."""

    hdr_source_max_luminance: int | None = None
    """Source mastering display max luminance in nits (e.g. 1000)."""

    hdr_source_max_cll: int | None = None
    """Source MaxCLL in nits (max content light level for the HDR title)."""

    # --- HDR — display capability (from !I50) ---
    display_supports_rec2020: bool | None = None
    """Whether the connected display reports Rec.2020 EDID support."""

    # --- Bookkeeping ---
    last_update_codes: tuple[str, ...] = field(default_factory=tuple)
    """Codes touched by the most recent protocol update (for debugging)."""
