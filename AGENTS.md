# AGENTS.md

Orientation for coding agents. Keep it short, keep it current, and describe
structure rather than features. `uv run agnix --strict .` checks this file with
[agnix](https://github.com/agent-sh/agnix).

## Overview

A CUPS driver and setup script that turn a Raspberry Pi into an AirPrint server
for the HP KE200 thermal label printer, a Bixolon-made printer that speaks
Bixolon's SLCS language. The vendor ships drivers only for macOS and Windows.
The [README](./README.md) covers setting up a Pi and printing.

Goals, in priority order:

1. **Runs on a stock Pi.** The filter runs on the system `python3` (Debian 12
   has 3.11) with the standard library only. A change that needs a package
   installed on the Pi is the wrong change.
2. **Byte-exact output.** The SLCS stream matches what the vendor's macOS filter
   sends. Change the commands only when a test print shows the printer needs it.
3. **Small.** CUPS does the PDF rendering and AirPrint; the filter only
   translates raster pages into printer commands.

Non-goals: other printer models, a web UI, printing without CUPS.

## Layout

| File                 | Role                                                                     |
| -------------------- | ------------------------------------------------------------------------ |
| `rastertohplabel.py` | The CUPS filter: reads CUPS raster on stdin or a file, writes SLCS.      |
| `HP-KE200.ppd`       | Label sizes and options, which CUPS also turns into the AirPrint offer.  |
| `install.sh`         | Installs both and creates the shared `HP_KE200` queue. Run on the Pi.    |
| `tests/`             | pytest tests that build raster pages and check the exact bytes sent.     |
| `vendor/`            | The vendor macOS driver, for reference. Gitignored: never commit it.     |

How a label travels:

```
Mac / iPhone ──AirPrint (PDF/URF)──▶ CUPS ──▶ cups-filters ──▶ raster ──▶ rastertohplabel ──▶ USB
```

Rules that keep that shape:

- **The protocol** is documented in the filter's module docstring and the
  README's protocol table. Keep both in step with the code.
- **The PPD's defaults** are the filter's defaults; a test checks they agree.
  PPD option choices are integers (or the `SensorType` keys), which the filter
  reads from the options CUPS passes, falling back to the PPD's defaults.
- **Raster input** can be uncompressed (`RaS3`, `RaSt`) or run-length encoded
  (`RaS2`), in either byte order, at 1 or 8 bits per pixel.

## Conventions

- Python follows PEP 8 with four spaces; everything else uses tabs, except
  JSON, TOML, YAML and Markdown at two spaces (`.editorconfig`). Ruff owns
  Python formatting and import order; shfmt owns `install.sh`.
- Ruff runs with every rule enabled (`select = ["ALL"]`). Suppress with
  `# noqa: <rule> -- <reason>`; a reason is required.
- mypy runs in strict mode. Reach for a type, not a cast or `Any`.
- Every module, class, function and constant gets a docstring saying what it is
  for. No parameter or return sections: the type hints carry them.
- Tests are pytest in `tests/`, named `test_*.py`, and build their own raster
  input rather than relying on fixture files.
- Commit messages and PR titles follow Conventional Commits; CI checks the title.

## Checks

```sh
uv sync
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
uv run shfmt --diff install.sh
shellcheck install.sh
uv run agnix --strict .
cupstestppd -I filters HP-KE200.ppd
```

Run the ones covering what you touched before committing. CI runs them all on
every pull request.

## Working here

- Change dependencies with uv; `uv.lock` isn't edited by hand. Renovate brings updates.
- If a dependency update breaks a check, fix the code rather than pinning the dependency back.
- Test on real hardware before changing what is sent to the printer: a unit test
  shows the bytes are what you meant, not that the printer accepts them.
