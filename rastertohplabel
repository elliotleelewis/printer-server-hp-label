#!/usr/bin/python3 -I
"""CUPS filter: CUPS raster -> Bixolon SLCS for the HP KE200 label printer.

Usage (called by CUPS): rastertohplabel job-id user title copies options [file]

Command stream reproduces what the vendor macOS filter (Bixolon
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

HEADER_SIZE = 1796
BAND_ROWS = 200
SENSORS = {"0Gap": b"G", "1Continuous": b"C", "2Blackmark": b"B"}

# cups_page_header2_t field offsets
OFF_NUM_COPIES = 340
OFF_WIDTH = 372
OFF_HEIGHT = 376
OFF_BITS_PER_COLOR = 384
OFF_BITS_PER_PIXEL = 388
OFF_BYTES_PER_LINE = 392
OFF_COLOR_SPACE = 400

CSPACE_W, CSPACE_K, CSPACE_SW = 0, 3, 18


def log(level, msg):
    sys.stderr.write(f"{level}: {msg}\n")
    sys.stderr.flush()


def read_exact(f, n):
    buf = f.read(n)
    if len(buf) < n:
        raise EOFError
    return buf


def ppd_defaults(path):
    opts = {}
    if not path:
        return opts
    try:
        with open(path, encoding="latin-1") as f:
            for line in f:
                if line.startswith("*Default"):
                    key, _, val = line[8:].partition(":")
                    opts[key.strip()] = val.strip()
    except OSError:
        pass
    return opts


def parse_options(s):
    opts = {}
    for item in s.split():
        key, _, val = item.partition("=")
        opts[key] = val or "true"
    return opts


class RasterReader:
    def __init__(self, f):
        self.f = f
        sync = read_exact(f, 4)
        if sync in (b"RaSt", b"RaS2", b"RaS3"):
            self.endian = ">"
        elif sync in (b"tSaR", b"2SaR", b"3SaR"):
            self.endian = "<"
        else:
            raise ValueError(f"not a CUPS raster stream: {sync!r}")
        self.compressed = sync in (b"RaS2", b"2SaR")

    def pages(self):
        while True:
            try:
                hdr = read_exact(self.f, HEADER_SIZE)
            except EOFError:
                return
            u = lambda off: struct.unpack_from(self.endian + "I", hdr, off)[0]
            page = {
                "width": u(OFF_WIDTH),
                "height": u(OFF_HEIGHT),
                "bits_per_color": u(OFF_BITS_PER_COLOR),
                "bits_per_pixel": u(OFF_BITS_PER_PIXEL),
                "bytes_per_line": u(OFF_BYTES_PER_LINE),
                "color_space": u(OFF_COLOR_SPACE),
                "copies": u(OFF_NUM_COPIES),
            }
            yield page, self.read_rows(page)

    def read_rows(self, page):
        bpl, height = page["bytes_per_line"], page["height"]
        if not self.compressed:
            for _ in range(height):
                yield read_exact(self.f, bpl)
            return
        # CUPS/PWG raster v2 run-length encoding
        bpp = max(1, page["bits_per_pixel"] // 8)
        clear = b"\xff" if page["color_space"] in (CSPACE_W, CSPACE_SW) else b"\x00"
        y = 0
        while y < height:
            repeat = read_exact(self.f, 1)[0] + 1
            line = bytearray()
            while len(line) < bpl:
                n = read_exact(self.f, 1)[0]
                if n == 128:
                    line += clear * (bpl - len(line))
                elif n > 128:
                    line += read_exact(self.f, (257 - n) * bpp)
                else:
                    line += read_exact(self.f, bpp) * (n + 1)
            line = bytes(line[:bpl])
            for _ in range(min(repeat, height - y)):
                yield line
                y += 1


def to_mono(row, page, out_bpl):
    """Convert one raster row to 1-bit, MSB first, 1 = black."""
    bits, cs, width = page["bits_per_pixel"], page["color_space"], page["width"]
    white_is_one = cs in (CSPACE_W, CSPACE_SW)
    if bits == 1:
        return bytes(b ^ 0xFF for b in row) if white_is_one else row
    if bits != 8:
        raise ValueError(f"unsupported bits per pixel: {bits}")
    out = bytearray(out_bpl)
    for x in range(width):
        v = row[x]
        black = v < 128 if white_is_one else v >= 128
        if black:
            out[x >> 3] |= 0x80 >> (x & 7)
    return bytes(out)


def main():
    if len(sys.argv) not in (6, 7):
        sys.stderr.write("Usage: rastertohplabel job-id user title copies options [file]\n")
        return 1

    opts = ppd_defaults(os.environ.get("PPD"))
    opts.update(parse_options(sys.argv[5]))
    darkness = max(0, min(20, int(opts.get("Darkness", "14"))))
    speed = max(1, min(5, int(opts.get("PrintSpeed", "4"))))
    sensor = SENSORS.get(opts.get("SensorType", "0Gap"), b"G")
    copies = max(1, int(sys.argv[4] or "1"))

    infile = open(sys.argv[6], "rb") if len(sys.argv) == 7 else sys.stdin.buffer
    out = sys.stdout.buffer
    reader = RasterReader(infile)

    pages = 0
    for page, rows in reader.pages():
        pages += 1
        width, height = page["width"], page["height"]
        out_bpl = (width + 7) // 8
        log("INFO", f"Printing label {pages} ({width}x{height} dots)")
        out.write(
            b"SL%d,24,%s\r\nSW%d\r\nSTd\r\nSD%d\r\nSM0,0\r\nSS%d\r\nTA0\r\n"
            % (height, sensor, width, darkness, speed)
        )

        band, band_y = [], 0
        for y, row in enumerate(rows):
            band.append(to_mono(row, page, out_bpl)[:out_bpl])
            if len(band) == BAND_ROWS or y == height - 1:
                data = b"".join(band)
                if any(data):  # skip blank bands; LD positions each band explicitly
                    out.write(b"LD" + struct.pack("<HHHH", 0, band_y, out_bpl, len(band)) + data)
                band_y += len(band)
                band = []

        out.write(b"\r\nP1" + (b",%d" % copies if copies > 1 else b"") + b"\r\n")
        out.flush()

    if not pages:
        log("ERROR", "No pages found")
        return 1
    log("INFO", "Ready to print.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
