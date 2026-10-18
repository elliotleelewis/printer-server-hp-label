"""Tests for the CUPS raster to SLCS filter."""

import io
import struct
from pathlib import Path

import pytest

import rastertohplabel as f

PPD = Path(__file__).parent.parent / "HP-KE200.ppd"
"""The PPD shipped with the filter."""

CSPACE_K = 3
"""CUPS_CSPACE_K: 1 bits are black."""

SETUP = b"SL%d,24,G\r\nSW%d\r\nSTd\r\nSD14\r\nSM0,0\r\nSS4\r\nTA0\r\n"
"""The setup commands sent for a page with default settings, given height and width."""


def header(  # noqa: PLR0913 -- one argument per header field the filter reads
    width: int,
    height: int,
    *,
    bits: int = 1,
    color_space: int = CSPACE_K,
    copies: int = 1,
    endian: str = "<",
) -> bytes:
    """Build a cups_page_header2_t with the fields the filter reads."""
    data = bytearray(f.HEADER_SIZE)
    bytes_per_line = (width * bits + 7) // 8
    for offset, value in (
        (340, copies),
        (f.OFF_WIDTH, width),
        (f.OFF_HEIGHT, height),
        (f.OFF_BITS_PER_PIXEL, bits),
        (f.OFF_BYTES_PER_LINE, bytes_per_line),
        (f.OFF_COLOR_SPACE, color_space),
    ):
        struct.pack_into(f"{endian}I", data, offset, value)
    return bytes(data)


def run(raster: bytes, settings: f.Settings | None = None) -> bytes:
    """Run the filter over a raster stream and return what it sends to the printer."""
    sink = io.BytesIO()
    f.convert(io.BytesIO(raster), sink, settings or f.Settings())
    return sink.getvalue()


def ld(y: int, bytes_per_line: int, rows: list[bytes]) -> bytes:
    """Build the LD command for a band of rows starting at `y`."""
    return b"LD" + struct.pack("<HHHH", 0, y, bytes_per_line, len(rows)) + b"".join(rows)


def test_uncompressed_page() -> None:
    """A 1-bit black-is-one page is sent as is, between the setup and print commands."""
    rows = [b"\x80\x01", b"\x00\x00", b"\xff\xff"]
    raster = b"3SaR" + header(16, 3) + b"".join(rows)
    assert run(raster) == SETUP % (3, 16) + ld(0, 2, rows) + b"\r\nP1\r\n"


def test_big_endian_header() -> None:
    """A big-endian stream is read the same as a little-endian one."""
    rows = [b"\xf0"]
    assert run(b"RaS3" + header(8, 1, endian=">") + rows[0]) == (
        SETUP % (1, 8) + ld(0, 1, rows) + b"\r\nP1\r\n"
    )


def test_compressed_page() -> None:
    """Run-length encoded rows decode to the same bitmap: repeats, literals and line clears."""
    width, height = 32, 3
    # Line repeated twice: 0xAA run twice, then 2 literal bytes (code 257 - 2).
    # Then one line: 0x81 run once, then the rest of the line cleared (code 128).
    encoded = bytes([1, 1, 0xAA, 255, 0x0F, 0xF0]) + bytes([0, 0, 0x81, 128])
    raster = b"2SaR" + header(width, height) + encoded
    rows = [b"\xaa\xaa\x0f\xf0", b"\xaa\xaa\x0f\xf0", b"\x81\x00\x00\x00"]
    assert run(raster) == SETUP % (height, width) + ld(0, 4, rows) + b"\r\nP1\r\n"


def test_white_is_one_is_inverted() -> None:
    """In the W colour space a set bit is white, so the bitmap is inverted."""
    raster = b"3SaR" + header(8, 1, color_space=f.CSPACE_W) + b"\x0f"
    assert ld(0, 1, [b"\xf0"]) in run(raster)


def test_grayscale_is_thresholded() -> None:
    """8-bit pixels become black at the threshold and above (K) or below it (W)."""
    pixels = bytes([0, 127, 128, 255, 0, 0, 0, 0])
    k = run(b"3SaR" + header(8, 1, bits=8) + pixels)
    w = run(b"3SaR" + header(8, 1, bits=8, color_space=f.CSPACE_W) + pixels)
    assert ld(0, 1, [b"\x30"]) in k
    assert ld(0, 1, [b"\xcf"]) in w


def test_unsupported_depth_raises() -> None:
    """Depths other than 1 and 8 bits are rejected rather than printed as noise."""
    with pytest.raises(ValueError, match="bits per pixel"):
        run(b"3SaR" + header(8, 1, bits=16) + bytes(16))


def test_bands_and_blank_bands() -> None:
    """Pages go in bands of 200 rows; all-white bands are left out without shifting the rest."""
    height = 450
    rows = [b"\x00"] * height
    rows[0] = rows[449] = b"\x01"
    out = run(b"3SaR" + header(8, height) + b"".join(rows))
    assert out.count(b"LD") == 2
    assert ld(0, 1, rows[0:200]) in out
    assert ld(400, 1, rows[400:450]) in out


def test_settings_are_sent() -> None:
    """Darkness, speed, sensor and copies reach the printer."""
    settings = f.Settings(darkness=20, speed=1, sensor=b"C", copies=3)
    out = run(b"3SaR" + header(8, 1) + b"\x00", settings)
    assert out.startswith(b"SL1,24,C\r\nSW8\r\nSTd\r\nSD20\r\nSM0,0\r\nSS1\r\nTA0\r\n")
    assert out.endswith(b"\r\nP1,3\r\n")


def test_multiple_pages() -> None:
    """Each page gets its own setup and print commands."""
    page = header(8, 1) + b"\x80"
    out = run(b"3SaR" + page + page)
    assert out.count(b"SL1,24,G") == 2
    assert out.count(b"P1\r\n") == 2


def test_truncated_page_raises() -> None:
    """A stream that ends mid-page is an error, not a short label."""
    with pytest.raises(EOFError):
        run(b"3SaR" + header(8, 2) + b"\x80")


def test_not_raster_raises() -> None:
    """Anything without a raster sync word is rejected."""
    with pytest.raises(ValueError, match="not a CUPS raster stream"):
        run(b"%PDF-1.7")


@pytest.mark.parametrize(
    ("options", "copies", "expected"),
    [
        ({}, "1", f.Settings()),
        ({"Darkness": "18", "PrintSpeed": "2"}, "2", f.Settings(darkness=18, speed=2, copies=2)),
        ({"Darkness": "99", "PrintSpeed": "0"}, "0", f.Settings(darkness=20, speed=1, copies=1)),
        ({"Darkness": "dark", "SensorType": "2Blackmark"}, "", f.Settings(sensor=b"B")),
    ],
)
def test_settings_from(options: dict[str, str], copies: str, expected: f.Settings) -> None:
    """Options are clamped to what the printer accepts, and bad values fall back to defaults."""
    assert f.settings_from(options, copies) == expected


def test_parse_options() -> None:
    """CUPS options parse into a dict, with bare names meaning true."""
    assert f.parse_options("Darkness=16 fit-to-page") == {"Darkness": "16", "fit-to-page": "true"}


def test_ppd_defaults_match_settings() -> None:
    """The shipped PPD's defaults are the filter's defaults."""
    defaults = f.ppd_defaults(str(PPD))
    assert defaults["PageSize"] == "w288h432"
    assert f.settings_from(defaults, "1") == f.Settings()


def test_ppd_defaults_missing_file() -> None:
    """A missing PPD gives no defaults rather than an error."""
    assert f.ppd_defaults("/nonexistent.ppd") == {}
    assert f.ppd_defaults(None) == {}


def test_main_reads_file(tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    """Given a file argument, main converts it and reports success to CUPS."""
    raster = tmp_path / "job.ras"
    raster.write_bytes(b"3SaR" + header(8, 1) + b"\x80")
    assert f.main(["rastertohplabel", "1", "user", "title", "1", "", str(raster)]) == 0
    captured = capsysbinary.readouterr()
    assert captured.out.endswith(b"\r\nP1\r\n")
    assert b"INFO: Ready to print." in captured.err


def test_main_rejects_empty_job(tmp_path: Path) -> None:
    """A raster stream with no pages fails the job."""
    raster = tmp_path / "job.ras"
    raster.write_bytes(b"3SaR")
    assert f.main(["rastertohplabel", "1", "user", "title", "1", "", str(raster)]) == 1


def test_main_usage() -> None:
    """The wrong number of arguments is a usage error."""
    assert f.main(["rastertohplabel"]) == 1
