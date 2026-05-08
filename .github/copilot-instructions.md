# VUU — Copilot Instructions

VUU is a PySide6 desktop GUI for reading/writing the 200 memory channels of the
Quanzheng UV-K5/UV-K6 handheld radio over a CH340 USB-serial cable. It also
imports/exports CHIRP CSV files.

## Stack & commands

- **Python 3.14+**, managed with [`uv`](https://docs.astral.sh/uv/). PySide6 + pyserial are the only runtime deps.
- Install / sync deps: `uv sync`
- Run the app: `uv run vuu` (entry point is `main:main` per `pyproject.toml`)
- Build wheel + sdist: `uv build` (Hatchling; the wheel only includes `main.py`, `uvk5.py`, and `assets/`)
- Run tests: `uv run pytest` (pytest config is in `pyproject.toml`; `tests/` is excluded from the wheel)
- Run a single test: `uv run pytest tests/test_uvk5.py::test_encode_channel_layout` or filter by name with `-k`
- Tests are hardware-free — they exercise pure functions in `uvk5.py` (CRC, XOR, tone parsing, channel encoding) and the CSV parsers in `main.py`. Do not introduce tests that require a real radio or open serial ports.
- No linter or type-checker is configured — do not invent one.

## Release flow

Versioning is fully driven by [Commitizen](https://commitizen-tools.github.io/commitizen/) + Conventional Commits.

- Bump + tag + changelog: `uv run cz bump --yes` then `git push --follow-tags`
- The `Release` workflow (`.github/workflows/release.yml`) runs on `v*` tags. It **verifies the tag matches `pyproject.toml`'s `version`**, builds with `uv build`, and uploads `dist/*.whl`, `dist/*.tar.gz`, and `install.sh` to a GitHub Release.
- Commit messages must follow `<type>(<scope>): <description>` (`feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `chore`, `build`, `ci`). `feat!:` or a `BREAKING CHANGE:` footer triggers a major bump.

## Architecture

Two-file design — keep it that way unless you have a strong reason:

- `uvk5.py` — pure protocol layer. No Qt, no GUI. Implements the k5prog-style framed serial protocol (XOR-obfuscated body, CRC16 that the radio actually sends as `0xFFFF`, magic `\xab\xcd ... \xdc\xba` framing). Public surface used by the GUI:
  - `handshake(ser) -> (firmware_version, session_id)`
  - `read_all_channels(ser, session_id) -> list[dict]`
  - `write_all_channels(ser, channels, session_id, progress=cb)`
  - `read_eeprom` / `write_eeprom` for raw access (max `0x80` bytes per call)
- `main.py` — Qt GUI. All serial I/O happens off the UI thread inside `ImportWorker` / `WriteWorker` `QThread` subclasses that emit `status`/`progress`/`finished`/`error` signals. Never call `uvk5.read_all_channels` / `write_all_channels` from the GUI thread.

### The channel dict (the contract between layers)

All channels are passed as plain `dict`s with this shape — both `uvk5.py` and the CSV parsers in `main.py` produce/consume it:

```
index:    int 0..199          name:    str (≤16 ASCII chars)
freq_hz:  int (Hz)            offset_hz: int (Hz)
duplex:   "" | "+" | "-"      mode:    "FM" | "NFM" | "AM"
tx_tone:  "None" | "<f> Hz" | "D<nnn>N"   (same shape for rx_tone)
power:    "Low (1.5W)" | "Med (3W)" | "High (5W)"
step_khz: float (must be one of uvk5.STEPS_KHZ)
bclo:     bool                scanlist1/scanlist2: bool
```

When adding a field, update **all** of: `_encode_channel`, `read_all_channels`, `_parse_csv_row`, `_parse_chirp_row`, and `COLUMNS` / `_refresh_table` in `main.py`.

### EEPROM map (don't change without a radio to test against)

- `0x0000–0x0C7F` — 200 × 16-byte channel entries (freq, offset, tones, flags, step)
- `0x0D60–0x0E27` — 200 × 1-byte attribute bytes (scanlist bits + `0x10` "free" flag)
- `0x0F50–0x1BCF` — 200 × 16-byte channel names

Reads/writes are chunked into `MEM_BLOCK = 0x80` requests. An "erased" slot has `freq_raw` of `0x00000000` or `0xFFFFFFFF` and the free bit set in its attr byte; `read_all_channels` skips these and `write_all_channels` re-erases any index not present in the input list.

## Conventions

- CSV import auto-detects format by column names: presence of `Location`+`Frequency` → CHIRP (`_parse_chirp_row`), `index`+`freq_hz` → VUU's own export format (`_parse_csv_row`). Add new formats by extending the dispatch in `MainWindow._import_csv`.
- Defensive parsing: row parsers return `None` to skip rather than raise. The sentinel `42949672950` (= `0xFFFFFFFF * 10` Hz) is treated as "erased, not real."
- Port discovery filters to CH340/CH341 (VID `0x1A86`) and falls back to all serial ports if none match — preserve this UX in `_refresh_ports`.
- Keep `uvk5.py` import-clean of Qt so it remains usable as a standalone library / from scripts.
