from __future__ import annotations

import asyncio

from PySide6 import QtCore, QtWidgets

from app.core.exam_controller import MAX_RIDERS, MIN_RIDERS
from app.ble.scanner import scan_ble_devices
from app.gui.no_wheel import NoWheelComboBox, NoWheelSpinBox


class ScanThread(QtCore.QThread):
    devices_found = QtCore.Signal(list)
    scan_failed = QtCore.Signal(str)

    def __init__(self, timeout: float = 6.0) -> None:
        super().__init__()
        self.timeout = timeout

    def run(self) -> None:
        try:
            devices = asyncio.run(scan_ble_devices(self.timeout))
            self.devices_found.emit([device.to_dict() for device in devices])
        except Exception as exc:
            self.scan_failed.emit(str(exc))


class ScanDialog(QtWidgets.QDialog):
    device_selected = QtCore.Signal(int, dict)
    log_message = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None, max_slot: int = MAX_RIDERS) -> None:
        super().__init__(parent)
        self.setWindowTitle("BLE 设备扫描")
        self.resize(920, 520)
        self._scan_thread: ScanThread | None = None
        self.max_slot = min(MAX_RIDERS, max(MIN_RIDERS, int(max_slot)))

        self.timeout_spin = NoWheelSpinBox()
        self.timeout_spin.setRange(2, 30)
        self.timeout_spin.setValue(6)
        self.timeout_spin.setSuffix(" s")

        self.scan_button = QtWidgets.QPushButton("扫描")
        self.scan_button.clicked.connect(self.start_scan)

        self.slot_combo = NoWheelComboBox()
        for slot in range(MIN_RIDERS, self.max_slot + 1):
            self.slot_combo.addItem(f"{slot}号分屏", slot)

        self.bind_button = QtWidgets.QPushButton("绑定到分屏")
        self.bind_button.clicked.connect(self.bind_selected)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("扫描时长"))
        controls.addWidget(self.timeout_spin)
        controls.addWidget(self.scan_button)
        controls.addStretch(1)
        controls.addWidget(self.slot_combo)
        controls.addWidget(self.bind_button)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["名称", "地址", "RSSI", "服务 UUID", "识别", "绑定"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        self.status_label = QtWidgets.QLabel("未扫描")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status_label)

    def start_scan(self) -> None:
        if self._scan_thread and self._scan_thread.isRunning():
            return

        self.table.setRowCount(0)
        self.status_label.setText("正在扫描...")
        self.scan_button.setEnabled(False)
        self._scan_thread = ScanThread(float(self.timeout_spin.value()))
        self._scan_thread.devices_found.connect(self.populate_devices)
        self._scan_thread.scan_failed.connect(self.scan_failed)
        self._scan_thread.finished.connect(lambda: self.scan_button.setEnabled(True))
        self._scan_thread.start()

    def populate_devices(self, devices: list[dict]) -> None:
        self.table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            service_uuids = device.get("service_uuids", []) or []
            values = [
                str(device.get("name") or "Unknown"),
                str(device.get("address") or ""),
                "" if device.get("rssi") is None else str(device.get("rssi")),
                ", ".join(service_uuids),
                str(device.get("support_label") or "未知"),
                "",
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, device)
                self.table.setItem(row, column, item)

            bind_item = QtWidgets.QTableWidgetItem("选择后绑定")
            self.table.setItem(row, 5, bind_item)

        self.status_label.setText(f"扫描完成，发现 {len(devices)} 个设备")
        self.log_message.emit(f"扫描完成，发现 {len(devices)} 个 BLE 设备")

    def scan_failed(self, message: str) -> None:
        self.status_label.setText(f"扫描失败: {message}")
        self.log_message.emit(f"扫描失败: {message}")

    def bind_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "提示", "请先选择一个设备")
            return

        item = self.table.item(row, 0)
        if item is None:
            return
        device = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(device, dict):
            return

        slot = int(self.slot_combo.currentData())
        self.device_selected.emit(slot, device)
        self.status_label.setText(f"已绑定到 {slot}号分屏")
