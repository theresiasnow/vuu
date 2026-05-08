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
        try:
            with serial.Serial(self.port, uvk5.BAUD_RATE, timeout=3) as ser:
                self.status.emit("Handshaking...")
                fw = uvk5.handshake(ser)
                self.status.emit(f"Connected — firmware: {fw}")
                self.status.emit("Reading channels...")
                channels = uvk5.read_all_channels(ser)
                self.finished.emit(channels)
        except Exception as exc:
            self.error.emit(str(exc))


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
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.port_combo.addItem(f"{p.device} — {p.description}", p.device)
        if not ports:
            self.port_combo.addItem("No ports found", "")
        self.status_bar.showMessage(f"{len(ports)} serial port(s) found")

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
        self.table.setRowCount(len(channels))
        for row, ch in enumerate(channels):
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

        self.import_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.status_bar.showMessage(f"Imported {len(channels)} channels.")

    def _on_error(self, msg: str):
        self.import_btn.setEnabled(True)
        QMessageBox.critical(self, "Import failed", msg)
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


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
