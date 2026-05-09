"""LumagenState dataclass tests."""

from __future__ import annotations

from dataclasses import replace

from pylumagen.state import Colorspace, HdrStatus, InputStatus, LumagenState, SourceMode


def test_default_state_has_expected_nulls() -> None:
    state = LumagenState()
    assert state.power_on is None
    assert state.alive is False
    assert state.model is None
    assert state.is_hdr is None
    assert state.last_update_codes == ()


def test_state_equality_is_field_wise() -> None:
    a = LumagenState(power_on=True, model="RadiancePro")
    b = LumagenState(power_on=True, model="RadiancePro")
    c = LumagenState(power_on=False, model="RadiancePro")
    assert a == b
    assert a != c


def test_state_replace_semantics() -> None:
    s = LumagenState()
    s2 = replace(s, power_on=True, is_hdr=True)
    assert s.power_on is None  # original untouched
    assert s2.power_on is True
    assert s2.is_hdr is True


def test_str_enums_round_trip() -> None:
    assert Colorspace("Rec.709") is Colorspace.REC_709
    assert HdrStatus("HDR") is HdrStatus.HDR
    assert InputStatus("Active") is InputStatus.ACTIVE
    assert SourceMode("p") is SourceMode.PROGRESSIVE
