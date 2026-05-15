"""VUU — UV-K5 channel importer GUI."""
import sys
from pathlib import Path
import serial.tools.list_ports
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QStatusBar, QMessageBox, QFileDialog, QProgressBar,
)
import csv
import uvk5


CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def app_icon() -> QIcon:
    base = Path(__file__).resolve().parent / "assets"
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256, 512):
        png = base / f"vuu-{size}.png"
        if png.exists():
            icon.addFile(str(png))
    if icon.isNull():
        svg = base / "vuu.svg"
        if svg.exists():
            icon.addFile(str(svg))
    return icon


COLUMNS = ["#", "Name", "Frequency", "Duplex", "Offset", "Mode", "TX Tone", "RX Tone", "Power", "Step", "BCLO"]


class ImportWorker(QThread):
    finished = Signal(list)
    error = Signal(str)
    status = Signal(str)

    def __init__(self, port: str):
        super().__init__()
        self.port = port

    def run(self):
        try:
            with serial.Serial(self.port, uvk5.BAUD_RATE, timeout=3) as ser:
                self.status.emit("Handshaking...")
                version, session_id = uvk5.handshake(ser)
                self.status.emit(f"Connected — firmware: {version}")
                self.status.emit("Reading channels...")
                channels = uvk5.read_all_channels(ser, session_id)
                self.finished.emit(channels)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


class WriteWorker(QThread):
    finished = Signal()
    error = Signal(str)
    status = Signal(str)
    progress = Signal(int, int)

    def __init__(self, port: str, channels: list[dict]):
        super().__init__()
        self.port = port
        self.channels = channels

    def run(self):
        try:
            with serial.Serial(self.port, uvk5.BAUD_RATE, timeout=3) as ser:
                self.status.emit("Handshaking...")
                version, session_id = uvk5.handshake(ser)
                self.status.emit(f"Connected — firmware: {version}")
                self.status.emit("Writing channels...")
                uvk5.write_all_channels(
                    ser, self.channels, session_id,
                    progress=lambda n, t: self.progress.emit(n, t),
                )
                self.finished.emit()
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VUU — UV-K5 Channel Importer")
        self.setWindowIcon(app_icon())
        self.resize(960, 600)
        self._channels: list[dict] = []
        self._worker: ImportWorker | None = None
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # --- top bar ---
        top = QHBoxLayout()
        top.addWidget(QLabel("Port:"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(160)
        top.addWidget(self.port_combo)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_ports)
        top.addWidget(self.refresh_btn)
        top.addSpacing(16)
        self.import_btn = QPushButton("Import from radio")
        self.import_btn.setDefault(True)
        self.import_btn.clicked.connect(self._start_import)
        top.addWidget(self.import_btn)
        self.write_btn = QPushButton("Write to radio")
        self.write_btn.setEnabled(False)
        self.write_btn.clicked.connect(self._start_write)
        top.addWidget(self.write_btn)
        top.addStretch()
        self.export_btn = QPushButton("Export CSV...")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_csv)
        top.addWidget(self.export_btn)
        self.import_csv_btn = QPushButton("Import CSV...")
        self.import_csv_btn.clicked.connect(self._import_csv)
        top.addWidget(self.import_csv_btn)
        root.addLayout(top)

        # --- table ---
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        # Interactive resize: we resizeColumnsToContents() once after a fill
        # rather than on every cell write (which would be O(rows*cols)).
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table)

        self.status_bar = QStatusBar()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.status_bar.addPermanentWidget(self.progress)
        self.setStatusBar(self.status_bar)

        self._refresh_ports()

    def _refresh_ports(self):
        self.port_combo.clear()
        all_ports = serial.tools.list_ports.comports()
        # UV-K5 uses a CH340 USB-serial chip (VID 0x1A86, PID 0x7523)
        radio_ports = [
            p for p in all_ports
            if getattr(p, "vid", None) == 0x1A86
            or "ch340" in (p.description or "").lower()
            or "ch341" in (p.description or "").lower()
        ]
        ports = radio_ports or all_ports
        for p in ports:
            self.port_combo.addItem(f"{p.device} — {p.description}", p.device)
        if not ports:
            self.port_combo.addItem("No ports found", "")
        if radio_ports:
            self.status_bar.showMessage(f"Found {len(radio_ports)} UV-K5 radio port(s)")
        elif all_ports:
            self.status_bar.showMessage(f"No radio detected — showing all {len(all_ports)} port(s)")
        else:
            self.status_bar.showMessage("No serial ports found")

    def _start_import(self):
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "No port", "Select a serial port first.")
            return
        self.import_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.table.setRowCount(0)
        self._worker = ImportWorker(port)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.status.connect(self.status_bar.showMessage)
        self._worker.start()

    def _on_finished(self, channels: list[dict]):
        self._channels = channels
        self._refresh_table()
        self.import_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.write_btn.setEnabled(bool(channels))
        self.status_bar.showMessage(f"Imported {len(channels)} channels.")

    def _start_write(self):
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "No port", "Select a serial port first.")
            return
        if not self._channels:
            return
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Warning)
        confirm.setWindowTitle("Write to radio")
        confirm.setText("Overwrite all 200 channel slots on the radio?")
        confirm.setInformativeText(
            f"{len(self._channels)} channel(s) will be written; "
            "remaining slots will be erased. This cannot be undone."
        )
        write_btn = confirm.addButton("Write", QMessageBox.DestructiveRole)
        confirm.addButton(QMessageBox.Cancel)
        confirm.exec()
        if confirm.clickedButton() is not write_btn:
            return

        self.import_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.write_btn.setEnabled(False)
        self.import_csv_btn.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self._writer = WriteWorker(port, self._channels)
        self._writer.progress.connect(self._on_write_progress)
        self._writer.status.connect(self.status_bar.showMessage)
        self._writer.finished.connect(self._on_write_finished)
        self._writer.error.connect(self._on_write_error)
        self._writer.start()

    def _on_write_progress(self, done: int, total: int):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status_bar.showMessage(f"Writing block {done}/{total}...")

    def _on_write_finished(self):
        self.progress.hide()
        self.import_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.write_btn.setEnabled(True)
        self.import_csv_btn.setEnabled(True)
        self.status_bar.showMessage(f"Wrote {len(self._channels)} channels to radio.")

    def _on_write_error(self, msg: str):
        self.progress.hide()
        self.import_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.write_btn.setEnabled(True)
        self.import_csv_btn.setEnabled(True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Write failed")
        box.setText("Write failed")
        box.setInformativeText(msg)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()
        self.status_bar.showMessage(f"Error: {msg}")

    def _refresh_table(self):
        n = len(self._channels)
        self.progress.setRange(0, n)
        self.progress.setValue(0)
        self.progress.show()
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(n)
            for row, ch in enumerate(self._channels):
                freq_mhz = ch["freq_hz"] / 1_000_000
                offset_mhz = ch["offset_hz"] / 1_000_000
                values = [
                    str(ch["index"] + 1),
                    ch["name"],
                    f"{freq_mhz:.5f} MHz",
                    ch["duplex"],
                    f"{offset_mhz:.4f} MHz" if ch["offset_hz"] else "",
                    ch["mode"],
                    ch["tx_tone"],
                    ch["rx_tone"],
                    ch["power"],
                    f"{ch['step_khz']} kHz",
                    "Yes" if ch["bclo"] else "",
                ]
                for col, val in enumerate(values):
                    item = QTableWidgetItem(val)
                    item.setTextAlignment(Qt.AlignCenter if col != 1 else Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(row, col, item)
                if row % 25 == 0:
                    self.progress.setValue(row)
                    QApplication.processEvents()
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.resizeColumnsToContents()
            self.progress.setValue(n)
            self.progress.hide()

    def _on_error(self, msg: str):
        self.import_btn.setEnabled(True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Import failed")
        box.setText("Import failed")
        box.setInformativeText(msg)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()
        self.status_bar.showMessage(f"Error: {msg}")

    def _export_csv(self):
        if not self._channels:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "channels.csv", "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self._channels[0].keys())
            writer.writeheader()
            writer.writerows(_csv_export_row(ch) for ch in self._channels)
        self.status_bar.showMessage(f"Saved {len(self._channels)} channels to {path}")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fields = set(reader.fieldnames or [])
                if "Location" in fields and "Frequency" in fields:
                    parser, fmt = _parse_chirp_row, "CHIRP"
                elif "index" in fields and "freq_hz" in fields:
                    parser, fmt = _parse_csv_row, "VUU"
                else:
                    QMessageBox.critical(
                        self, "Unknown CSV format",
                        f"Could not recognize CSV columns: {sorted(fields)}",
                    )
                    return
                rows = []
                for r in reader:
                    parsed = parser(r)
                    if parsed is None:
                        continue
                    if not (0 <= parsed["index"] < 200):
                        continue
                    rows.append(parsed)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"Could not read CSV:\n{exc}")
            return
        if not rows:
            QMessageBox.warning(self, "Empty CSV", "No usable channels found in file.")
            return

        mode = "replace"
        if self._channels:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Import CSV")
            box.setText(f"Loaded {len(rows)} channels from {fmt} CSV.")
            box.setInformativeText(
                "Replace current channels, or merge by channel index "
                "(CSV entries overwrite matching slots, others kept)?"
            )
            replace_btn = box.addButton("Replace", QMessageBox.DestructiveRole)
            merge_btn = box.addButton("Merge", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.setDefaultButton(merge_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is replace_btn:
                mode = "replace"
            elif clicked is merge_btn:
                mode = "merge"
            else:
                return

        if mode == "replace":
            self._channels = sorted(rows, key=lambda c: c["index"])
        else:
            by_index = {c["index"]: c for c in self._channels}
            for r in rows:
                by_index[r["index"]] = r
            self._channels = sorted(by_index.values(), key=lambda c: c["index"])

        self._refresh_table()
        self.export_btn.setEnabled(True)
        self.write_btn.setEnabled(bool(self._channels))
        self.status_bar.showMessage(
            f"{mode.capitalize()}d {len(rows)} channel(s) from {fmt} CSV — total now {len(self._channels)}."
        )


def _csv_safe_cell(value):
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped and stripped[0] in CSV_FORMULA_PREFIXES:
        return f"'{value}"
    return value


def _csv_export_row(row: dict) -> dict:
    return {key: _csv_safe_cell(value) for key, value in row.items()}


def _parse_csv_row(row: dict) -> dict | None:
    """Coerce VUU-format CSV strings back into the channel-dict types used internally."""
    def _to_bool(v):
        return str(v).strip().lower() in ("1", "true", "yes", "y")
    def _norm(v):
        s = (v or "").strip()
        return s if s and s != "?" else "None"
    try:
        index = int(row["index"])
        freq_hz = int(row["freq_hz"])
        offset_hz = int(row.get("offset_hz") or 0)
        step_khz = float(row.get("step_khz") or 5.0)
    except (TypeError, ValueError, KeyError):
        return None
    # 0xFFFFFFFF * 10 = 42949672950 Hz — erased EEPROM slot, not a real channel.
    if freq_hz <= 0 or freq_hz >= 42949672950:
        return None
    if offset_hz < 0 or offset_hz >= 42949672950:
        offset_hz = 0
    return {
        "index": index,
        "name": row.get("name", "").strip(),
        "freq_hz": freq_hz,
        "offset_hz": offset_hz,
        "duplex": row.get("duplex", "") or "",
        "tx_tone": _norm(row.get("tx_tone")),
        "rx_tone": _norm(row.get("rx_tone")),
        "mode": row.get("mode", "FM") or "FM",
        "power": row.get("power", "Low (1.5W)") or "Low (1.5W)",
        "step_khz": step_khz,
        "bclo": _to_bool(row.get("bclo")),
        "scanlist1": _to_bool(row.get("scanlist1")),
        "scanlist2": _to_bool(row.get("scanlist2")),
    }


def _parse_chirp_row(row: dict) -> dict | None:
    """Convert a CHIRP CSV row into VUU's internal channel dict, or None to skip."""
    try:
        loc = int(row["Location"])
        freq_mhz = float(row["Frequency"])
    except (TypeError, ValueError, KeyError):
        return None

    def _ctcss(v):
        try:
            return f"{float(v):.1f} Hz"
        except (TypeError, ValueError):
            return "None"

    def _dcs(v):
        try:
            return f"D{int(v):03d}N"
        except (TypeError, ValueError):
            return "None"

    tone_kind = (row.get("Tone") or "").strip()
    rtone, ctone = row.get("rToneFreq"), row.get("cToneFreq")
    dcs_code, rx_dcs = row.get("DtcsCode"), row.get("RxDtcsCode")
    if tone_kind == "Tone":
        tx_tone, rx_tone = _ctcss(rtone), "None"
    elif tone_kind in ("TSQL", "TSQL-R"):
        tx_tone = rx_tone = _ctcss(ctone)
    elif tone_kind in ("DTCS", "DTCS-R"):
        tx_tone = _dcs(dcs_code)
        rx_tone = _dcs(rx_dcs or dcs_code)
    else:
        tx_tone = rx_tone = "None"

    duplex = (row.get("Duplex") or "").strip()
    if duplex not in ("", "+", "-"):
        duplex = ""

    chirp_mode = (row.get("Mode") or "FM").upper()
    mode = chirp_mode if chirp_mode in ("FM", "NFM", "AM") else "FM"

    try:
        step_khz = float(row.get("TStep") or 5.0)
    except ValueError:
        step_khz = 5.0

    pwr = (row.get("Power") or "").lower()
    if "high" in pwr or pwr.startswith("5"):
        power = "High (5W)"
    elif "mid" in pwr or "med" in pwr or pwr.startswith("3"):
        power = "Med (3W)"
    else:
        power = "Low (1.5W)"

    try:
        offset_hz = round(float(row.get("Offset") or 0) * 1_000_000)
    except ValueError:
        offset_hz = 0
    if not duplex:
        offset_hz = 0

    return {
        "index": loc - 1,
        "name": (row.get("Name") or "").strip(),
        "freq_hz": round(freq_mhz * 1_000_000),
        "offset_hz": offset_hz,
        "duplex": duplex,
        "tx_tone": tx_tone,
        "rx_tone": rx_tone,
        "mode": mode,
        "power": power,
        "step_khz": step_khz,
        "bclo": False,
        "scanlist1": (row.get("Skip") or "").strip().upper() != "S",
        "scanlist2": False,
    }


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("VUU")
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
