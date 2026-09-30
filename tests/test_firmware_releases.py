"""Tests for the release-index parser and the release-zip unpacker.

The HTML below is **synthetic**. It mimics the vendor page's Squarespace nesting
and reproduces its known quirks (a revision listed twice, a mistyped heading,
several label spellings), but every word of note text is invented and nothing is
copied from the real page. The zips are built in-test with dummy bytes; no vendor
binary is involved.
"""

from __future__ import annotations

import dataclasses
import datetime
import io
import zipfile

import pytest

import aiolumagen
import aiolumagen.firmware
from aiolumagen.exceptions import LumagenFirmwareError, LumagenFirmwareImageError
from aiolumagen.firmware import releases
from aiolumagen.firmware.protocol import FirmwareRevision
from aiolumagen.firmware.releases import (
    RELEASES_URL,
    LumagenReleaseIndexError,
    ReleaseChannel,
    ReleaseListing,
    extract_updater_zip,
    latest_release,
    parse_release_index,
)


def _entry(href: str, heading: str, body: str) -> str:
    return (
        f'<li><p><a href="{href}">Download</a></p>'
        f"<ul><li><p>{heading}&nbsp; {body}</p></li></ul></li>"
    )


SYNTHETIC_INDEX = (
    "<html><body><h1>Synthetic updates page</h1>"
    '<p><a href="#listtotable">Download</a> the table below.</p>'
    "<ul>"
    + _entry(
        "/s/radiance_pro030326.zip",
        "<strong>Beta 030326-</strong><em>Posted 042826</em>",
        "Adds the imaginary widget.<br><strong><em>Update time ~1 minutes @230k "
        "from previous firmware.</em></strong>",
    )
    + _entry(
        "/s/radiance_pro120325.zip",
        "<strong>Beta 120325-</strong><em>Posted 121025</em>",
        "Reworks the pretend scaler. Ask <a href='mailto:someone@example.com'>support</a>"
        " for details.<br><strong><em>Update time ~5 minutes @230k.</em></strong>",
    )
    + _entry(
        "/s/radiance_pro112325.zip",
        "<strong>Beta 112325-</strong><em>Posted 112525</em>",
        "first copy of the invented notes.",
    )
    + _entry(
        "/s/radiance_pro112325.zip",
        "<strong>Beta 112325-</strong><em>Posted 112425</em>",
        "second copy with other invented notes.",
    )
    + _entry(
        "https://www.lumagen.com/s/radiance_pro030225.zip",
        "<strong>Production 030225-</strong><em>Posted 031025</em>",
        "Stable made-up release.",
    )
    + _entry(
        "/s/radiance_pro101524.zip",
        "<strong>Beta 1015524-</strong><em>Posted 991524</em>",
        "Heading has a typo in this fake entry.",
    )
    + _entry(
        "/s/radiance_pro090922.zip",
        "<strong>Production candidate 090922-</strong><em>Posted 091222</em>",
        "Candidate build of nothing in particular.",
    )
    + _entry(
        "/s/radiance_pro060623.zip",
        "<strong>060623-</strong>",
        "Unlabelled synthetic entry.",
    )
    + "</ul><p>Footer text should not leak</p>"
    '<p><a href="mailto:sales@example.com">Contact</a></p>'
    "</body></html>"
)


@pytest.fixture
def listings() -> tuple[ReleaseListing, ...]:
    return parse_release_index(SYNTHETIC_INDEX)


def _by_rev(listings: tuple[ReleaseListing, ...], mmddyy: str) -> ReleaseListing:
    (match,) = [listing for listing in listings if listing.revision.mmddyy == mmddyy]
    return match


class TestParseReleaseIndex:
    def test_revisions_are_deduped_and_chronological(
        self, listings: tuple[ReleaseListing, ...]
    ) -> None:
        assert [listing.revision.mmddyy for listing in listings] == [
            "090922",
            "060623",
            "101524",
            "030225",
            "112325",
            "120325",
            "030326",
        ]

    def test_chronological_not_integer_ordering(self, listings: tuple[ReleaseListing, ...]) -> None:
        revisions = [listing.revision.mmddyy for listing in listings]
        assert int("101524") > int("030225")
        assert revisions.index("101524") < revisions.index("030225")
        assert listings[-1].revision == FirmwareRevision.parse("030326")

    def test_href_wins_over_a_mistyped_heading(self, listings: tuple[ReleaseListing, ...]) -> None:
        listing = _by_rev(listings, "101524")
        assert listing.revision == FirmwareRevision(year=2024, month=10, day=15)
        assert listing.label == "Beta"
        assert listing.channel is ReleaseChannel.BETA
        assert "1015524" not in listing.notes

    def test_channel_mapping(self, listings: tuple[ReleaseListing, ...]) -> None:
        production = _by_rev(listings, "030225")
        assert (production.label, production.channel) == ("Production", ReleaseChannel.PRODUCTION)

        beta = _by_rev(listings, "030326")
        assert (beta.label, beta.channel) == ("Beta", ReleaseChannel.BETA)

        candidate = _by_rev(listings, "090922")
        assert candidate.label == "Production candidate"
        assert candidate.channel is ReleaseChannel.BETA

        unlabelled = _by_rev(listings, "060623")
        assert unlabelled.label == ""
        assert unlabelled.channel is ReleaseChannel.BETA

    def test_label_takes_only_known_words(self) -> None:
        # A heading without a revision token: note words must not join the label.
        html = "<ul>" + _entry("/s/radiance_pro030326.zip", "Beta Fixes HDR", "More.") + "</ul>"
        (listing,) = parse_release_index(html)
        assert listing.label == "Beta"
        assert listing.notes.startswith("Fixes HDR")

    def test_posted_date(self, listings: tuple[ReleaseListing, ...]) -> None:
        assert _by_rev(listings, "030326").posted == datetime.date(2026, 4, 28)
        assert _by_rev(listings, "101524").posted is None  # "991524" isn't a date
        assert _by_rev(listings, "060623").posted is None  # absent

    def test_estimate(self, listings: tuple[ReleaseListing, ...]) -> None:
        assert _by_rev(listings, "030326").est_minutes == 1
        assert _by_rev(listings, "120325").est_minutes == 5
        assert _by_rev(listings, "060623").est_minutes is None

    def test_notes_are_cleaned(self, listings: tuple[ReleaseListing, ...]) -> None:
        notes = _by_rev(listings, "030326").notes
        assert notes == "Adds the imaginary widget."
        for listing in listings:
            assert "Posted" not in listing.notes
            assert "Update time" not in listing.notes
            assert "Download" not in listing.notes
            assert not listing.notes.startswith(("Beta", "Production"))
            assert "\xa0" not in listing.notes
            assert "  " not in listing.notes
            assert "Footer text" not in listing.notes
        assert _by_rev(listings, "060623").notes == "Unlabelled synthetic entry."
        assert _by_rev(listings, "090922").notes == "Candidate build of nothing in particular."

    def test_link_text_inside_notes_is_kept(self, listings: tuple[ReleaseListing, ...]) -> None:
        notes = _by_rev(listings, "120325").notes
        assert notes == "Reworks the pretend scaler. Ask support for details."

    def test_duplicate_keeps_the_first_occurrence(
        self, listings: tuple[ReleaseListing, ...]
    ) -> None:
        listing = _by_rev(listings, "112325")
        assert "first copy" in listing.notes
        assert listing.posted == datetime.date(2025, 11, 25)

    def test_urls(self, listings: tuple[ReleaseListing, ...]) -> None:
        latest = _by_rev(listings, "030326")
        assert latest.url == "https://www.lumagen.com/s/radiance_pro030326.zip"
        assert latest.filename == "radiance_pro030326.zip"
        assert _by_rev(listings, "030225").url == (
            "https://www.lumagen.com/s/radiance_pro030225.zip"
        )

    def test_unrelated_anchors_are_ignored(self) -> None:
        html = (
            '<a href="#listtotable">x</a><a href="mailto:a@example.com">y</a>'
            '<a href="/s/manual.pdf">z</a>'
            + _entry("/s/radiance_pro030326.zip", "Beta 030326-", "Only entry.")
        )
        (listing,) = parse_release_index(html)
        assert listing.revision.mmddyy == "030326"

    def test_listing_is_frozen_and_hashable(self, listings: tuple[ReleaseListing, ...]) -> None:
        listing = listings[-1]
        with pytest.raises(dataclasses.FrozenInstanceError):
            listing.notes = "changed"  # type: ignore[misc]
        assert len(set(listings)) == len(listings)
        assert RELEASES_URL.startswith("https://www.lumagen.com/")

    @pytest.mark.parametrize(
        "html",
        [
            pytest.param("", id="empty"),
            pytest.param(
                '<ul><li><a href="#listtotable">Download</a></li></ul>', id="no-release-anchors"
            ),
            pytest.param(_entry("/s/radiance_pro133125.zip", "Beta 133125-", "x"), id="bad-month"),
            pytest.param(_entry("/s/radiance_proABC.zip", "Beta", "x"), id="not-digits"),
            pytest.param(
                _entry("http://www.lumagen.com/s/radiance_pro030326.zip", "Beta", "x"),
                id="plain-http",
            ),
            pytest.param(
                _entry("https://evil.example/s/radiance_pro030326.zip", "Beta", "x"),
                id="off-site",
            ),
        ],
    )
    def test_fails_closed(self, html: str) -> None:
        with pytest.raises(LumagenReleaseIndexError) as info:
            parse_release_index(html)
        assert isinstance(info.value, LumagenFirmwareError)
        assert isinstance(info.value, ValueError)

    def test_one_bad_entry_rejects_the_whole_page(self) -> None:
        html = SYNTHETIC_INDEX.replace(
            "</ul><p>Footer", _entry("/s/radiance_pro999999.zip", "Beta", "bad") + "</ul><p>Footer"
        )
        with pytest.raises(LumagenReleaseIndexError):
            parse_release_index(html)


class TestLatestRelease:
    def test_beta_is_newest_of_any_label(self, listings: tuple[ReleaseListing, ...]) -> None:
        latest = latest_release(listings, ReleaseChannel.BETA)
        assert latest is not None
        assert latest.revision.mmddyy == "030326"

    def test_production_is_newest_production(self, listings: tuple[ReleaseListing, ...]) -> None:
        latest = latest_release(listings, ReleaseChannel.PRODUCTION)
        assert latest is not None
        assert latest.revision.mmddyy == "030225"

    def test_production_with_none_available(self, listings: tuple[ReleaseListing, ...]) -> None:
        betas = [listing for listing in listings if listing.channel is ReleaseChannel.BETA]
        assert latest_release(betas, ReleaseChannel.PRODUCTION) is None

    def test_empty(self) -> None:
        assert latest_release((), ReleaseChannel.BETA) is None
        assert latest_release((), ReleaseChannel.PRODUCTION) is None


EXE = b"MZ" + b"\0" * 64


def _zip(members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class TestExtractUpdaterZip:
    def test_flat_zip(self) -> None:
        data = _zip({"radiance_pro030326.exe": EXE, "Tip0006_Synthetic.pdf": b"%PDF-"})
        assert extract_updater_zip(data) == ("radiance_pro030326.exe", EXE)

    def test_exe_in_a_folder_returns_the_basename(self) -> None:
        data = _zip({"release/radiance_pro030326.exe": EXE})
        assert extract_updater_zip(data) == ("radiance_pro030326.exe", EXE)

    def test_case_insensitive_name(self) -> None:
        data = _zip({"Radiance_Pro030326.EXE": EXE})
        assert extract_updater_zip(data) == ("Radiance_Pro030326.EXE", EXE)

    @pytest.mark.parametrize(
        "members",
        [
            pytest.param({"readme.pdf": b"%PDF-"}, id="no-exe"),
            pytest.param({"setup.exe": EXE}, id="wrong-exe"),
            pytest.param(
                {"radiance_pro030326.exe": EXE, "old/radiance_pro120325.exe": EXE},
                id="two-exes",
            ),
        ],
    )
    def test_requires_exactly_one_updater(self, members: dict[str, bytes]) -> None:
        with pytest.raises(LumagenFirmwareImageError, match="exactly one"):
            extract_updater_zip(_zip(members))

    @pytest.mark.parametrize(
        "bad_name",
        [
            "../radiance_pro030326.exe",
            "/abs/x.pdf",
            "..\\x.pdf",
            "C:\\temp\\x.pdf",
            "\\\\server\\share\\x.pdf",
            "docs/../../x.pdf",
        ],
    )
    def test_rejects_path_traversal(self, bad_name: str) -> None:
        data = _zip({"radiance_pro030326.exe": EXE, bad_name: b"x"})
        with pytest.raises(LumagenFirmwareImageError, match="unsafe path"):
            extract_updater_zip(data)

    def test_rejects_non_zip_bytes(self) -> None:
        with pytest.raises(LumagenFirmwareImageError, match="not a zip"):
            extract_updater_zip(b"<html>not a zip</html>")

    def test_rejects_too_many_entries(self) -> None:
        members = {f"file{i}.txt": b"" for i in range(releases.MAX_ZIP_ENTRIES)}
        members["radiance_pro030326.exe"] = EXE
        with pytest.raises(LumagenFirmwareImageError, match="entries"):
            extract_updater_zip(_zip(members))

    def test_rejects_an_encrypted_member(self) -> None:
        # zipfile won't write an encrypted member, so set the flag bit afterwards
        # in both the local header (offset 6) and the central directory (offset 8).
        data = bytearray(_zip({"radiance_pro030326.exe": EXE}))
        data[data.index(b"PK\x03\x04") + 6] |= 0x1
        data[data.index(b"PK\x01\x02") + 8] |= 0x1
        with pytest.raises(LumagenFirmwareImageError, match="encrypted"):
            extract_updater_zip(bytes(data))

    def test_rejects_an_undecodable_utf8_flagged_name(self) -> None:
        # zipfile sets general-purpose bit 11 (UTF-8 names) for a non-ASCII name.
        # Swap the name's UTF-8 bytes for invalid ones of the same length in both
        # headers, so zipfile raises UnicodeDecodeError while reading the archive.
        data = bytes(_zip({"radiance_pro030326.exe": EXE, "caf\u00e9.pdf": b"x"}))
        assert data.count("\u00e9".encode()) == 2
        data = data.replace("\u00e9".encode(), b"\xff\xfe")
        with pytest.raises(LumagenFirmwareImageError, match="corrupt") as err:
            extract_updater_zip(data)
        assert isinstance(err.value.__cause__, UnicodeDecodeError)

    def test_rejects_oversize(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(releases, "MAX_UPDATER_EXE_BYTES", 16)
        with pytest.raises(LumagenFirmwareImageError, match="at most 16"):
            extract_updater_zip(_zip({"radiance_pro030326.exe": EXE}))

    def test_rejects_a_corrupt_crc(self) -> None:
        payload = b"MZ" + bytes(range(64))
        data = bytearray(_zip({"radiance_pro030326.exe": payload}, compression=zipfile.ZIP_STORED))
        offset = data.index(payload) + 10
        data[offset] ^= 0xFF
        with pytest.raises(LumagenFirmwareImageError, match="corrupt"):
            extract_updater_zip(bytes(data))

    def test_errors_are_image_errors(self) -> None:
        with pytest.raises(ValueError):
            extract_updater_zip(b"")


class TestExports:
    NAMES = (
        "ReleaseListing",
        "ReleaseChannel",
        "LumagenReleaseIndexError",
        "RELEASES_URL",
        "parse_release_index",
        "latest_release",
        "extract_updater_zip",
    )

    @pytest.mark.parametrize("name", NAMES)
    def test_exported_from_firmware_only(self, name: str) -> None:
        assert name in aiolumagen.firmware.__all__
        assert hasattr(aiolumagen.firmware, name)
        assert name not in aiolumagen.__all__
