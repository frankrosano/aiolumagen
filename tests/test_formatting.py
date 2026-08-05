"""Wire-code decoder tests.

Pure functions — no protocol, no transport. These pin the two derivations that
aren't obvious from the wire bytes: the NTSC ``/1.001`` rate family and the
width-from-vertical-plus-aspect calculation.
"""

from __future__ import annotations

import pytest

from aiolumagen.formatting import (
    decode_aspect_ratio,
    decode_output_mask,
    decode_vertical_rate,
    derive_horizontal_resolution,
)

# ---------- Vertical rate ----------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        # NTSC family: the code is the truncated integer, so the real rate is
        # (code + 1) / 1.001 — one BELOW the nominal value.
        ("023", 23.976),
        ("029", 29.97),
        ("047", 47.952),
        ("059", 59.94),
        ("071", 71.928),
        ("095", 95.904),
        ("119", 119.88),
        # Integer rates decode to themselves.
        ("024", 24.0),
        ("025", 25.0),
        ("030", 30.0),
        ("050", 50.0),
        ("060", 60.0),
        ("120", 120.0),
    ],
)
def test_decode_vertical_rate(code: str, expected: float) -> None:
    assert decode_vertical_rate(code) == pytest.approx(expected)


def test_decode_vertical_rate_distinguishes_5994_from_60() -> None:
    """The whole point of the table: 059 and 060 are different rates.

    A naive int() would report both as 59 and 60, losing the distinction that
    matters most for judging a signal path.
    """
    assert decode_vertical_rate("059") != decode_vertical_rate("060")
    assert decode_vertical_rate("060") == 60.0


@pytest.mark.parametrize("code", ["000", "0000", "", "   ", "abc", "-1"])
def test_decode_vertical_rate_returns_none_for_no_signal_or_garbage(
    code: str,
) -> None:
    """``000`` is the device's no-signal placeholder, not 0 Hz."""
    assert decode_vertical_rate(code) is None


# ---------- Aspect ratio ----------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("133", 1.33),
        ("178", 1.78),
        ("185", 1.85),
        ("235", 2.35),
        ("237", 2.37),
        ("240", 2.4),
    ],
)
def test_decode_aspect_ratio(code: str, expected: float) -> None:
    assert decode_aspect_ratio(code) == pytest.approx(expected)


@pytest.mark.parametrize("code", ["000", "", "abc"])
def test_decode_aspect_ratio_returns_none_for_no_signal_or_garbage(
    code: str,
) -> None:
    assert decode_aspect_ratio(code) is None


# ---------- Horizontal resolution ----------


@pytest.mark.parametrize(
    ("vertical", "aspect", "expected"),
    [
        # The raw product is a few pixels off because the aspect code carries
        # only two decimals; snapping recovers the exact standard width.
        ("2160", "178", 3840),  # 3844.8 -> 3840
        ("1080", "178", 1920),  # 1922.4 -> 1920
        ("720", "178", 1280),  # 1281.6 -> 1280
        ("480", "133", 640),  # 638.4  -> 640
        ("1080", "237", 2560),  # 2559.6 -> 2560 (2560x1080 ultrawide)
    ],
)
def test_derive_horizontal_resolution_snaps_to_standard_widths(
    vertical: str, aspect: str, expected: int
) -> None:
    assert derive_horizontal_resolution(vertical, aspect) == expected


def test_derive_horizontal_resolution_refuses_a_distant_snap() -> None:
    """Outside the tolerance the computed width is kept, not forced to a standard.

    Snapping unconditionally to the nearest common width is the obvious
    implementation and it's wrong: a 2.37 raster at 2160 lines is ~5119 px
    wide, and the nearest common width is 4096 — a 20% error reported as if it
    were exact. Keeping the computed value bounds the error to the aspect
    code's own precision instead.
    """
    assert derive_horizontal_resolution("2160", "237") == 5119


@pytest.mark.parametrize(
    ("vertical", "aspect"),
    [
        ("0000", "178"),  # no signal
        ("2160", "000"),  # no aspect reported
        ("", "178"),
        ("abc", "178"),
        ("2160", "abc"),
    ],
)
def test_derive_horizontal_resolution_returns_none_when_unusable(
    vertical: str, aspect: str
) -> None:
    assert derive_horizontal_resolution(vertical, aspect) is None


# ---------- Output enable mask ----------


def test_decode_output_mask_doc_example() -> None:
    """Tip0011: "16 bit hex, b0 to 15 for out 1 to 16. Bit=1 if on".

    ``000e`` is ``0b1110`` — outputs 2, 3, 4 on, output 1 off. Taken from the
    real !I25 capture in test_protocol.py.
    """
    assert decode_output_mask("000e") == (2, 3, 4)


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        ("0001", (1,)),
        ("000f", (1, 2, 3, 4)),
        ("8000", (16,)),
        (0x000E, (2, 3, 4)),
        (0, ()),
    ],
)
def test_decode_output_mask_accepts_hex_or_int(
    mask: str | int, expected: tuple[int, ...]
) -> None:
    assert decode_output_mask(mask) == expected


def test_decode_output_mask_zero_is_empty_not_none() -> None:
    """All outputs off is a real state; distinguish it from "couldn't read"."""
    assert decode_output_mask("0000") == ()
    assert decode_output_mask("") is None


@pytest.mark.parametrize("mask", ["", "   ", "zzzz", "nope"])
def test_decode_output_mask_returns_none_for_garbage(mask: str) -> None:
    assert decode_output_mask(mask) is None
