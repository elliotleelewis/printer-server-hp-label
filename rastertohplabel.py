#!/usr/bin/python3 -I
r"""CUPS filter: CUPS raster to Bixolon SLCS for the HP KE200 label printer.

Usage (called by CUPS): rastertohplabel job-id user title copies options [file]

The command stream reproduces what the vendor macOS filter (Bixolon
rastertoHPLabel v1.0.1) sends, per page:

    SL<length>,24,<G|C|B>\r\n   label length in dots, gap, sensor
    SW<width>\r\n                label width in dots
    STd\r\n                      direct thermal
    SD<0-20>\r\n                 darkness
    SM0,0\r\n                    margins
    SS<1-5>\r\n                  speed
    TA0\r\n                      tear-off offset
    LD <x,y,bytesPerLine,rows as uint16 LE> <1-bit rows, 1 = black>
    \r\nP1[,copies]\r\n          print
"""

import os
import struct
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

HEADER_SIZE: Final = 1796
"""Size of a cups_page_header2_t, which follows every page's sync word."""

BAND_ROWS: Final = 200
"""Most rows sent in one LD command, as the vendor filter does."""

GAP_DOTS: Final = 24
"""Gap between labels, in dots, sent with SL."""

SENSORS: Final = {"0Gap": b"G", "1Continuous": b"C", "2Blackmark": b"B"}
"""SensorType PPD choices, mapped to the SL sensor letter."""

DARKNESS_RANGE: Final = (0, 20)
"""Lowest and highest SD values the printer accepts."""

SPEED_RANGE: Final = (1, 5)
"""Lowest and highest SS values the printer accepts."""

DEFAULT_DARKNESS: Final = 14
"""Darkness when neither the job nor the PPD sets one."""

DEFAULT_SPEED: Final = 4
"""Speed when neither the job nor the PPD sets one."""

SYNC_BIG_ENDIAN: Final = (b"RaSt", b"RaS2", b"RaS3")
"""Raster sync words for a stream with big-endian headers."""

SYNC_LITTLE_ENDIAN: Final = (b"tSaR", b"2SaR", b"3SaR")
"""Raster sync words for a stream with little-endian headers."""

SYNC_COMPRESSED: Final = (b"RaS2", b"2SaR")
"""Raster sync words for a stream whose rows are run-length encoded."""

OFF_WIDTH: Final = 372
"""Offset of cupsWidth in the page header."""

OFF_HEIGHT: Final = 376
"""Offset of cupsHeight in the page header."""

OFF_BITS_PER_PIXEL: Final = 388
"""Offset of cupsBitsPerPixel in the page header."""

OFF_BYTES_PER_LINE: Final = 392
"""Offset of cupsBytesPerLine in the page header."""

OFF_COLOR_SPACE: Final = 400
"""Offset of cupsColorSpace in the page header."""

CSPACE_W: Final = 0
"""CUPS_CSPACE_W: luminance, where 1 bits (or high values) are white."""

CSPACE_SW: Final = 18
"""CUPS_CSPACE_SW: sRGB luminance, where high values are white."""

RLE_CLEAR_LINE: Final = 128
"""Run-length code meaning the rest of the line is the clear colour."""

RLE_LITERAL_BASE: Final = 257
"""Run-length codes above 128 are this minus the count of literal pixels."""

GRAY_THRESHOLD: Final = 128
"""Halfway point used to turn 8-bit pixels into black or white."""


@dataclass(frozen=True, slots=True)
class PageHeader:
    """The fields of a CUPS raster page header that the filter uses."""

    width: int
    """Page width in dots."""

    height: int
    """Page height in dots."""

    bits_per_pixel: int
    """1 for bitmaps, 8 for grayscale."""

    bytes_per_line: int
    """Bytes in one row of the raster."""

    color_space: int
    """CUPS colour space, which says whether 1 means black or white."""

    @property
    def white_is_one(self) -> bool:
        """Whether a set bit, or a high value, is white rather than black."""
        return self.color_space in {CSPACE_W, CSPACE_SW}


@dataclass(frozen=True, slots=True)
class Settings:
    """Printer settings applied to every page of a job."""

    darkness: int = DEFAULT_DARKNESS
    """SD value, 0-20."""

    speed: int = DEFAULT_SPEED
    """SS value, 1-5 (3-7 in/s)."""

    sensor: bytes = b"G"
    """SL sensor letter: gap, continuous or black mark."""

    copies: int = 1
    """Copies of each page, sent with P1."""


def read_exact(stream: BinaryIO, size: int) -> bytes:
    """Read exactly `size` bytes, raising EOFError if the stream ends first."""
    data = stream.read(size)
    if len(data) < size:
        raise EOFError
    return data


def ppd_defaults(path: str | None) -> dict[str, str]:
    """Read the `*Default<Option>` values from a PPD file."""
    defaults: dict[str, str] = {}
    if not path:
        return defaults
    try:
        with Path(path).open(encoding="latin-1") as ppd:
            for line in ppd:
                if line.startswith("*Default"):
                    key, _, value = line.removeprefix("*Default").partition(":")
                    defaults[key.strip()] = value.strip()
    except OSError:
        pass
    return defaults


def parse_options(options: str) -> dict[str, str]:
    """Parse the CUPS options argument, `name=value name2=value2 ...`."""
    parsed: dict[str, str] = {}
    for item in options.split():
        key, _, value = item.partition("=")
        parsed[key] = value or "true"
    return parsed


def int_option(
    options: Mapping[str, str],
    key: str,
    default: int,
    bounds: tuple[int, int],
) -> int:
    """Read an integer option, falling back to `default` and clamping to `bounds`."""
    try:
        value = int(options.get(key, default))
    except ValueError:
        value = default
    return max(bounds[0], min(bounds[1], value))


def settings_from(options: Mapping[str, str], copies: str) -> Settings:
    """Build the job's settings from its options and copies argument."""
    try:
        count = max(1, int(copies or "1"))
    except ValueError:
        count = 1
    return Settings(
        darkness=int_option(options, "Darkness", DEFAULT_DARKNESS, DARKNESS_RANGE),
        speed=int_option(options, "PrintSpeed", DEFAULT_SPEED, SPEED_RANGE),
        sensor=SENSORS.get(options.get("SensorType", ""), b"G"),
        copies=count,
    )


class RasterReader:
    """Reads pages from a CUPS raster stream (v1, v2 compressed, or v3)."""

    def __init__(self, stream: BinaryIO) -> None:
        """Read the stream's sync word to learn its byte order and encoding."""
        sync = read_exact(stream, 4)
        if sync in SYNC_BIG_ENDIAN:
            self._endian = ">"
        elif sync in SYNC_LITTLE_ENDIAN:
            self._endian = "<"
        else:
            msg = f"not a CUPS raster stream: {sync!r}"
            raise ValueError(msg)
        self._stream = stream
        self._compressed = sync in SYNC_COMPRESSED

    def pages(self) -> Iterator[tuple[PageHeader, Iterator[bytes]]]:
        """Yield each page's header and rows; read the rows before the next page."""
        while True:
            try:
                header = read_exact(self._stream, HEADER_SIZE)
            except EOFError:
                return
            page = PageHeader(
                width=self._field(header, OFF_WIDTH),
                height=self._field(header, OFF_HEIGHT),
                bits_per_pixel=self._field(header, OFF_BITS_PER_PIXEL),
                bytes_per_line=self._field(header, OFF_BYTES_PER_LINE),
                color_space=self._field(header, OFF_COLOR_SPACE),
            )
            yield page, self._rows(page)

    def _field(self, header: bytes, offset: int) -> int:
        """Read one unsigned 32-bit header field."""
        value: int = struct.unpack_from(f"{self._endian}I", header, offset)[0]
        return value

    def _rows(self, page: PageHeader) -> Iterator[bytes]:
        """Yield the page's rows, decoding run-length encoding if the stream uses it."""
        if not self._compressed:
            for _ in range(page.height):
                yield read_exact(self._stream, page.bytes_per_line)
            return
        y = 0
        while y < page.height:
            repeat = read_exact(self._stream, 1)[0] + 1
            line = self._decode_line(page)
            for _ in range(min(repeat, page.height - y)):
                yield line
                y += 1

    def _decode_line(self, page: PageHeader) -> bytes:
        """Decode one run-length encoded row (the CUPS/PWG raster v2 scheme)."""
        pixel_bytes = max(1, page.bits_per_pixel // 8)
        clear = b"\xff" if page.white_is_one else b"\x00"
        line = bytearray()
        while len(line) < page.bytes_per_line:
            code = read_exact(self._stream, 1)[0]
            if code == RLE_CLEAR_LINE:
                line += clear * (page.bytes_per_line - len(line))
            elif code > RLE_CLEAR_LINE:
                line += read_exact(self._stream, (RLE_LITERAL_BASE - code) * pixel_bytes)
            else:
                line += read_exact(self._stream, pixel_bytes) * (code + 1)
        return bytes(line[: page.bytes_per_line])


def to_mono(row: bytes, page: PageHeader) -> bytes:
    """Convert one raster row to a 1-bit row, MSB first, where 1 is black."""
    out_bytes = (page.width + 7) // 8
    if page.bits_per_pixel == 1:
        row = row[:out_bytes]
        return bytes(b ^ 0xFF for b in row) if page.white_is_one else row
    if page.bits_per_pixel != 8:  # noqa: PLR2004 -- the one other depth supported
        msg = f"unsupported bits per pixel: {page.bits_per_pixel}"
        raise ValueError(msg)
    out = bytearray(out_bytes)
    for x, value in enumerate(row[: page.width]):
        black = value < GRAY_THRESHOLD if page.white_is_one else value >= GRAY_THRESHOLD
        if black:
            out[x >> 3] |= 0x80 >> (x & 7)
    return bytes(out)


def encode_page(page: PageHeader, rows: Iterator[bytes], settings: Settings) -> Iterator[bytes]:
    """Yield the SLCS commands that print one page."""
    yield (
        b"SL%d,%d,%s\r\nSW%d\r\nSTd\r\nSD%d\r\nSM0,0\r\nSS%d\r\nTA0\r\n"
        % (page.height, GAP_DOTS, settings.sensor, page.width, settings.darkness, settings.speed)
    )
    out_bytes = (page.width + 7) // 8
    band: list[bytes] = []
    band_y = 0
    for y, row in enumerate(rows):
        band.append(to_mono(row, page))
        if len(band) == BAND_ROWS or y == page.height - 1:
            data = b"".join(band)
            # Blank bands are skipped: each LD positions itself, so nothing shifts.
            if any(data):
                yield b"LD" + struct.pack("<HHHH", 0, band_y, out_bytes, len(band)) + data
            band_y += len(band)
            band = []
    copies = b",%d" % settings.copies if settings.copies > 1 else b""
    yield b"\r\nP1" + copies + b"\r\n"


def convert(source: BinaryIO, sink: BinaryIO, settings: Settings) -> int:
    """Convert a raster stream to SLCS, returning the number of pages."""
    pages = 0
    for page, rows in RasterReader(source).pages():
        pages += 1
        log("INFO", f"Printing label {pages} ({page.width}x{page.height} dots)")
        sink.writelines(encode_page(page, rows, settings))
        sink.flush()
    return pages


def log(level: str, message: str) -> None:
    """Send a message to CUPS, which reads `LEVEL: message` lines from stderr."""
    sys.stderr.write(f"{level}: {message}\n")
    sys.stderr.flush()


def main(argv: list[str]) -> int:
    """Run as a CUPS filter, returning the exit status."""
    if len(argv) not in {6, 7}:
        sys.stderr.write("Usage: rastertohplabel job-id user title copies options [file]\n")
        return 1
    options = ppd_defaults(os.environ.get("PPD"))
    options.update(parse_options(argv[5]))
    settings = settings_from(options, argv[4])
    try:
        if len(argv) == 7:  # noqa: PLR2004 -- the optional file argument
            with Path(argv[6]).open("rb") as source:
                pages = convert(source, sys.stdout.buffer, settings)
        else:
            pages = convert(sys.stdin.buffer, sys.stdout.buffer, settings)
    except (EOFError, ValueError) as error:
        log("ERROR", f"Bad raster data: {error or 'stream ended mid-page'}")
        return 1
    if not pages:
        log("ERROR", "No pages found")
        return 1
    log("INFO", "Ready to print.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
