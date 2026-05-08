"""VUU — UV-K5 channel importer GUI."""
import sys
import serial.tools.list_ports
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QStatusBar, QMessageBox, QFileDialog,
)
import csv
import uvk5


COLUMNS = ["#", "Name", "Frequency", "Duplex", "Offset", "Mode", "TX Tone", "RX Tone", "Power", "Step", "BCLO"]


class ImportWorker(QThread):
    finished = Signal(list)
    error = Signal(str)
    status = Signal(str)

    def __init__(self, port: str):
        super().__init__()
        self.port = port

    def run(self):
        import traceback
        try:
            with serial.Serial(self.port, uvk5.BAUD_RATE, timeout=3) as ser:
                self.status.emit("Handshaking...")
                version, session_id = uvk5.handshake(ser)
                self.status.emit(f"Connected — firmware: {version}")
                self.status.emit("Reading channels...")
                channels = uvk5.read_all_channels(ser, session_id)
                self.finished.emit(channels)
        except Exception as exc:
            traceback.print_exc()
            self.error.emit(f"{type(exc).__name__}: {exc}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VUU — UV-K5 Channel Importer")
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
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        root.addWidget(self.table)

        self.status_bar = QStatusBar()
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
        self.status_bar.showMessage(f"Imported {len(channels)} channels.")

    def _refresh_table(self):
        self.table.setRowCount(len(self._channels))
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
            writer.writerows(self._channels)
        self.status_bar.showMessage(f"Saved {len(self._channels)} channels to {path}")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open CSV", "", "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rows = [_parse_csv_row(r) for r in csv.DictReader(f)]
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", f"Could not read CSV:\n{exc}")
            return
        if not rows:
            QMessageBox.warning(self, "Empty CSV", "No channels found in file.")
            return

        mode = "replace"
        if self._channels:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Import CSV")
            box.setText(f"Loaded {len(rows)} channels from CSV.")
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
        self.status_bar.showMessage(
            f"{mode.capitalize()}d {len(rows)} channel(s) from CSV — total now {len(self._channels)}."
        )


def _parse_csv_row(row: dict) -> dict:
    """Coerce CSV strings back into the channel-dict types used internally."""
    def _to_bool(v):
        return str(v).strip().lower() in ("1", "true", "yes", "y")
    return {
        "index": int(row["index"]),
        "name": row.get("name", "").strip(),
        "freq_hz": int(row["freq_hz"]),
        "offset_hz": int(row.get("offset_hz") or 0),
        "duplex": row.get("duplex", "") or "",
        "tx_tone": row.get("tx_tone", "None") or "None",
        "rx_tone": row.get("rx_tone", "None") or "None",
        "mode": row.get("mode", "FM") or "FM",
        "power": row.get("power", "Low (1.5W)") or "Low (1.5W)",
        "step_khz": float(row.get("step_khz") or 5.0),
        "bclo": _to_bool(row.get("bclo")),
        "scanlist1": _to_bool(row.get("scanlist1")),
        "scanlist2": _to_bool(row.get("scanlist2")),
    }


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
