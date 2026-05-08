# VUU — UV-K5 Channel Importer

Cross-platform GUI for the **Quanzheng UV-K5 / UV-K6** family of
VHF/UHF handheld radios. Read, edit, import (CHIRP CSV) and write
all 200 memory channels over USB.

## Features

- 🔌 Read all 200 channels from the radio over the CH340 USB cable
- 📤 Write channels back to the radio (with progress + confirmation)
- 📥 Import **CHIRP CSV** files (and VUU's own CSV format)
- 📦 Export channels to CSV
- 🛡️ Skips erased EEPROM slots, sanitises bogus offsets
- 🔧 Works against stock and custom firmware (egzumer, IJV, VUURWERK, …)

## Install

### One-line install (macOS / Linux)

```bash
curl -fsSL https://github.com/theresiasnow/vuu/releases/latest/download/install.sh | bash
```

The script downloads the latest wheel and installs it with
`uv tool` (preferred), `pipx`, or `pip --user` — whichever is on
your `PATH`. Then run:

```bash
vuu
```

### Manual install

Download the wheel from the [latest release](https://github.com/theresiasnow/vuu/releases/latest)
and install it with your favourite tool:

```bash
uv tool install vuu-*.whl       # or
pipx install vuu-*.whl          # or
pip install --user vuu-*.whl
```

### From source

```bash
git clone https://github.com/theresiasnow/vuu.git
cd vuu
uv sync
uv run vuu
```

Requires **Python 3.14+**, PySide6, and pyserial.

## Usage

1. Plug in your UV-K5 with the programming cable.
2. Launch `vuu` (or `uv run vuu` from a checkout).
3. Pick the serial port (CH340 ports are auto-filtered to the top).
4. **Import from radio** → reads all channels into the table.
5. Optionally **Import CSV…** to load a CHIRP file (replace or merge by index).
6. **Write to radio** → confirms, then writes all 200 slots
   (unused indices are erased). Progress is shown per EEPROM block.

## Supported radios

- Quanzheng **UV-K5**, **UV-K5(8)**, **UV-K6** (CH340 USB-serial chip).
- Stock and custom firmware — channel layout is identical across them.

## License

MIT
