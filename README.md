# HP KE200 label printer server

Turn a Raspberry Pi into a wireless AirPrint server for the **HP KE200** (HPKE200) 4×6 thermal shipping label printer, which officially only has drivers for macOS and Windows.

Once set up, the printer appears as **HP Label Printer @ &lt;hostname&gt;** on every Mac, iPhone and iPad on the network, with no drivers to install.

## How it works

The KE200 is a Bixolon-made printer (its USB device ID reports `CMD:Bixolon_01`) that speaks Bixolon's SLCS label language. This project provides:

- **`rastertohplabel.py`**: a CUPS filter (Python 3, standard library only) that converts CUPS raster pages into SLCS commands
- **`HP-KE200.ppd`**: a printer description with 4×6, 4×4 and 2×4 in label sizes, darkness, speed and media sensor options
- **`install.sh`**: installs both and creates a shared CUPS queue

```
Mac / iPhone ──AirPrint──▶ CUPS on the Pi ──▶ PDF → raster ──▶ rastertohplabel ──USB──▶ KE200
```

CUPS does the PDF rendering and the AirPrint/Bonjour advertising, and the filter only handles the printer's own commands.

## Hardware

- Any Raspberry Pi with Wi-Fi. A **Pi Zero 2 W** is plenty. Avoid the original Pi Zero W, whose single core makes PDF rendering slow.
- For a Pi Zero, a **micro-USB OTG adapter or cable** to connect the printer.
- A reliable power supply and a decent microSD card.

## Setup

1. **Flash the SD card** with [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
   choose *Raspberry Pi OS Lite (64-bit)*, then in the OS customisation settings set a hostname (e.g. `labelprinter`), your Wi-Fi network and password, and enable SSH.

2. **Boot the Pi**, plug in and switch on the printer, then SSH in:

   ```sh
   ssh <user>@labelprinter.local
   ```

3. **Install CUPS and this project:**

   ```sh
   sudo apt update
   sudo apt install -y cups cups-filters avahi-daemon git
   git clone https://github.com/elliotleelewis/printer-server-hp-label.git
   cd printer-server-hp-label
   sudo ./install.sh
   ```

   `install.sh` finds the printer on USB, installs the filter and PPD, creates a queue called `HP_KE200`, shares it on the local network, and stops CUPS from shutting itself down when idle (which would make the printer disappear from other devices' printer lists).

4. **Print a test label** from the Pi:

   ```sh
   lp -d HP_KE200 -o fit-to-page some-label.pdf
   ```

## Printing

**Mac:** add the printer by address, not from the default Bonjour list. From that list macOS treats it as a shared CUPS printer and only offers *Generic PostScript Printer*, which has no label sizes.

1. System Settings → Printers & Scanners → Add Printer, Scanner or Fax… → **IP** tab
2. Address `labelprinter.local`, Protocol **AirPrint**, Queue `printers/HP_KE200`
3. Wait for **Use** to show **HP KE200 Label Printer (SLCS)**, the driver macOS builds over AirPrint, then click Add

In the print dialog choose paper size **4 x 6 in** and, if the label is offset or too small, tick *Scale to fit*.

**iPhone / iPad:** Share → Print → select the printer.

To change the defaults (darkness, speed, sensor type), open `http://labelprinter.local:631/printers/HP_KE200` → *Administration* → *Set Default Options*, or use `lpadmin`:

```sh
sudo lpadmin -p HP_KE200 -o Darkness=16
```

## Troubleshooting

- **Printer not found by `install.sh`:** check `lsusb` lists `03f0:013b HP KE200`. If `dmesg` shows `Cannot enable. Maybe the USB cable is bad?`, try another port, cable or adapter. On a Pi 4 one of the USB 2 ports wouldn't connect but a blue USB 3 port worked.
- **Printer not showing on the Mac:** check the Pi is on the same network and run `avahi-browse -rt _ipp._tcp` on the Pi to confirm it's being advertised.
- **Faint or patchy print:** raise `Darkness` (0–20, default 14).
- **Job errors:** see `/var/log/cups/error_log`. For more detail run `sudo cupsctl --debug-logging`, print again, then `sudo cupsctl --no-debug-logging`.

## Protocol notes

Worked out by analysing the vendor's macOS CUPS filter (for interoperability) and confirmed with test prints. Each page is sent as:

| Command | Meaning |
|---|---|
| `SL<rows>,24,G\r\n` | Label length in dots, 24-dot gap, sensor (`G` gap, `C` continuous, `B` black mark) |
| `SW<dots>\r\n` | Label width in dots |
| `STd\r\n` | Direct thermal media |
| `SD<0-20>\r\n` | Darkness |
| `SM0,0\r\n` | X/Y margin |
| `SS<1-5>\r\n` | Print speed (3–7 in/s) |
| `TA0\r\n` | Tear-off offset |
| `LD` + `x, y, bytesPerLine, rows` (uint16 LE) + bitmap | 1-bit bitmap rows, MSB first, 1 = black; sent in bands of ≤ 200 rows |
| `\r\nP1[,copies]\r\n` | Print |

The printer is 203 dpi, so a 4×6 in label is 812 × 1218 dots.

## Development

Development needs [uv](https://docs.astral.sh/uv/); the Pi itself doesn't. [AGENTS.md](./AGENTS.md) covers the layout and conventions.

```sh
uv sync                       # ruff, mypy, pytest, shfmt and agnix
uv run pytest                 # tests
uv run ruff check             # lint
uv run ruff format            # format
uv run mypy                   # type check
```

CI also runs shellcheck and `cupstestppd` (from `cups-client`). Commit messages and PR titles follow [Conventional Commits](https://www.conventionalcommits.org).

## License

[MIT](LICENSE)

## Disclaimer

This is an independent project, not affiliated with or endorsed by HP Inc., C&A Marketing or Bixolon. "HP" and other names are trademarks of their respective owners and are used only to identify compatible hardware. No vendor software is included or required.
