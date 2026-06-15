from __future__ import annotations

import asyncio
import json
import time

from PySide6 import QtCore, QtWidgets

from app.ble.device_client import MockTrainerDeviceClient, TrainerDeviceClient
from app.core.exam_controller import (
    EXAM_MODE_ROUTE,
    EXAM_MODE_TIME,
    ExamController,
    default_config_path,
)
from app.core.exporter import export_exam_csv
from app.core.rider_state import STATUS_CONNECTING, STATUS_DISCONNECTED
from app.core.route import RouteProfile, RouteSegment, load_route, save_route
from app.core.settings import AppSettings, load_settings, save_settings
from app.gui.rider_panel import RiderPanel
from app.gui.route_profile_widget import RouteProfileWidget
from app.gui.scan_dialog import ScanDialog
from app.utils.logger import get_app_logger


class BleRuntime(QtCore.QThread):
    power_received = QtCore.Signal(int, int, float)
    status_changed = QtCore.Signal(int, str, str)
    log_message = QtCore.Signal(str)

    def __init__(self, bindings: list[dict], mock: bool = False) -> None:
        super().__init__()
        self.bindings = bindings
        self.mock = mock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._clients: list[object] = []
        self._clients_by_slot: dict[int, object] = {}

    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self.log_message.emit(f"BLE 运行线程异常: {exc}")

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def set_grade(self, slot: int, grade_percent: float) -> None:
        if not self._loop:
            return

        def schedule() -> None:
            client = self._clients_by_slot.get(slot)
            if client is None:
                return
            setter = getattr(client, "set_simulation_grade", None)
            if setter is not None:
                asyncio.create_task(setter(grade_percent))

        self._loop.call_soon_threadsafe(schedule)

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._clients = []
        self._clients_by_slot = {}
        tasks: list[asyncio.Task] = []

        for binding in self.bindings:
            slot = int(binding.get("slot", 0))
            if slot < 1 or slot > 4:
                continue

            if self.mock:
                client = MockTrainerDeviceClient(
                    slot=slot,
                    power_callback=self._emit_power,
                    status_callback=self._emit_status,
                    log_callback=self.log_message.emit,
                )
            else:
                address = str(binding.get("device_address") or "")
                name = str(binding.get("device_name") or address)
                if not address:
                    self.status_changed.emit(slot, STATUS_DISCONNECTED, "未绑定设备")
                    continue
                client = TrainerDeviceClient(
                    slot=slot,
                    address=address,
                    name=name,
                    power_callback=self._emit_power,
                    status_callback=self._emit_status,
                    log_callback=self.log_message.emit,
                )

            self._clients.append(client)
            self._clients_by_slot[slot] = client
            tasks.append(asyncio.create_task(client.run()))

        if not tasks:
            self.log_message.emit("没有可连接的设备")
            return

        stop_task = asyncio.create_task(self._stop_event.wait())
        client_tasks: set[asyncio.Task] = set(tasks)
        while client_tasks and not stop_task.done():
            done, _pending = await asyncio.wait(
                client_tasks | {stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                break
            client_tasks -= done

        for client in self._clients:
            stop = getattr(client, "stop", None)
            if stop is not None:
                await stop()

        stop_task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _emit_power(self, slot: int, power: int, timestamp: float) -> None:
        self.power_received.emit(slot, power, timestamp)

    def _emit_status(self, slot: int, status: str, message: str) -> None:
        self.status_changed.emit(slot, status, message)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, mock: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("多人骑行台功率考试软件")
        self.setMinimumSize(800, 600)
        self.resize(1180, 760)

        self.logger = get_app_logger()
        self.settings = load_settings()
        if mock:
            self.settings.mock_mode = True
        self.controller = ExamController(duration_seconds=60)
        self.route_profile = load_route()
        self.controller.set_exam_mode(self.settings.exam_mode)
        self.controller.set_duration(self.settings.duration_seconds)
        self.controller.set_bike_weight(self.settings.bike_weight_kg)
        self.controller.set_route(self.route_profile)
        self.config_path = default_config_path()
        self.ble_runtime: BleRuntime | None = None
        self.panels: dict[int, RiderPanel] = {}
        self._settings_table_blocked = False
        self._applying_settings_widgets = False
        self._last_grade_push_at = 0.0

        self._build_ui()
        self._load_config()
        self._populate_route_table()
        self._populate_rider_settings_table()
        self._refresh_all_panels()
        self._update_exam_route_visibility()

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        controls = QtWidgets.QFrame()
        controls.setObjectName("controlBar")
        controls_layout = QtWidgets.QHBoxLayout(controls)
        controls_layout.setContentsMargins(10, 8, 10, 8)
        controls_layout.setSpacing(8)

        self.prepare_button = QtWidgets.QPushButton("准备考试")
        self.prepare_button.clicked.connect(self._prepare_exam)
        self.start_button = QtWidgets.QPushButton("开始考试")
        self.start_button.clicked.connect(self._start_exam)
        self.start_button.setEnabled(False)
        self.stop_button = QtWidgets.QPushButton("终止考试")
        self.stop_button.clicked.connect(self._terminate_exam)
        self.stop_button.setEnabled(False)
        self.reset_button = QtWidgets.QPushButton("重置")
        self.reset_button.clicked.connect(self._reset_exam)
        self.export_button = QtWidgets.QPushButton("导出 CSV")
        self.export_button.clicked.connect(self._export_csv)
        self.export_button.setEnabled(False)

        self.mode_status_label = QtWidgets.QLabel("")
        controls_layout.addWidget(self.mode_status_label)
        controls_layout.addStretch(1)
        for button in [
            self.prepare_button,
            self.start_button,
            self.stop_button,
            self.reset_button,
            self.export_button,
        ]:
            controls_layout.addWidget(button)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_exam_page(), "考试")
        self.tabs.addTab(self._build_route_page(), "赛道")
        self.tabs.addTab(self._build_settings_page(), "设置")
        self.tabs.addTab(self._build_log_page(), "日志")

        root.addWidget(controls)
        root.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.setStyleSheet(self._stylesheet())

    def _build_exam_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page_layout = QtWidgets.QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(8)

        self.exam_route_profile_widget = RouteProfileWidget()
        self.exam_route_profile_widget.setMinimumHeight(150)
        self.exam_route_profile_widget.setMaximumHeight(180)
        self.exam_route_profile_widget.set_route(self.route_profile)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        container = QtWidgets.QWidget()
        panels_grid = QtWidgets.QGridLayout(container)
        panels_grid.setContentsMargins(0, 0, 0, 0)
        panels_grid.setSpacing(8)
        for slot in range(1, 5):
            panel = RiderPanel(slot)
            panel.rider_name_changed.connect(self._rider_name_changed)
            panel.rider_weight_changed.connect(self._rider_weight_changed)
            panel.set_inputs_locked(True)
            self.panels[slot] = panel
            panels_grid.addWidget(panel, (slot - 1) // 2, (slot - 1) % 2)
        panels_grid.setRowStretch(0, 1)
        panels_grid.setRowStretch(1, 1)
        panels_grid.setColumnStretch(0, 1)
        panels_grid.setColumnStretch(1, 1)

        scroll.setWidget(container)
        page_layout.addWidget(self.exam_route_profile_widget)
        page_layout.addWidget(scroll)
        return page

    def _build_route_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.route_profile_widget = RouteProfileWidget()
        self.route_profile_widget.set_route(self.route_profile)

        self.route_table = QtWidgets.QTableWidget(0, 2)
        self.route_table.setHorizontalHeaderLabels(["距离 m", "坡度 %"])
        self.route_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.route_table.verticalHeader().setVisible(False)
        self.route_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.route_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

        button_row = QtWidgets.QHBoxLayout()
        self.add_segment_button = QtWidgets.QPushButton("新增路段")
        self.add_segment_button.clicked.connect(self._add_route_segment)
        self.remove_segment_button = QtWidgets.QPushButton("删除路段")
        self.remove_segment_button.clicked.connect(self._remove_route_segment)
        self.move_segment_up_button = QtWidgets.QPushButton("上移")
        self.move_segment_up_button.clicked.connect(lambda: self._move_route_segment(-1))
        self.move_segment_down_button = QtWidgets.QPushButton("下移")
        self.move_segment_down_button.clicked.connect(lambda: self._move_route_segment(1))
        self.apply_route_button = QtWidgets.QPushButton("应用赛道")
        self.apply_route_button.clicked.connect(self._apply_route_from_table)
        self.save_route_button = QtWidgets.QPushButton("保存赛道")
        self.save_route_button.clicked.connect(self._save_route_from_table)
        self.route_total_label = QtWidgets.QLabel("")

        for button in [
            self.add_segment_button,
            self.remove_segment_button,
            self.move_segment_up_button,
            self.move_segment_down_button,
            self.apply_route_button,
            self.save_route_button,
        ]:
            button_row.addWidget(button)
        button_row.addStretch(1)
        button_row.addWidget(self.route_total_label)

        layout.addWidget(self.route_profile_widget)
        layout.addWidget(self.route_table, 1)
        layout.addLayout(button_row)
        return page

    def _build_settings_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        exam_box = QtWidgets.QGroupBox("考试设置")
        exam_layout = QtWidgets.QGridLayout(exam_box)
        exam_layout.setHorizontalSpacing(10)
        exam_layout.setVerticalSpacing(8)

        self.exam_mode_combo = QtWidgets.QComboBox()
        self.exam_mode_combo.addItem("固定时长", EXAM_MODE_TIME)
        self.exam_mode_combo.addItem("固定线路", EXAM_MODE_ROUTE)
        self.exam_mode_combo.currentIndexChanged.connect(self._settings_changed)

        self.duration_combo = QtWidgets.QComboBox()
        for label, seconds in [
            ("1分钟", 60),
            ("3分钟", 180),
            ("5分钟", 300),
            ("10分钟", 600),
            ("20分钟", 1200),
            ("自定义", -1),
        ]:
            self.duration_combo.addItem(label, seconds)
        self.duration_combo.currentIndexChanged.connect(self._duration_changed)

        self.custom_seconds = QtWidgets.QSpinBox()
        self.custom_seconds.setRange(1, 24 * 3600)
        self.custom_seconds.setValue(self.settings.duration_seconds)
        self.custom_seconds.setSuffix(" 秒")
        self.custom_seconds.valueChanged.connect(self._settings_changed)

        self.bike_weight_spin = QtWidgets.QDoubleSpinBox()
        self.bike_weight_spin.setRange(5.0, 30.0)
        self.bike_weight_spin.setDecimals(1)
        self.bike_weight_spin.setSingleStep(0.5)
        self.bike_weight_spin.setSuffix(" kg")
        self.bike_weight_spin.setValue(self.settings.bike_weight_kg)
        self.bike_weight_spin.valueChanged.connect(self._settings_changed)

        self.mock_checkbox = QtWidgets.QCheckBox("Mock 模式")
        self.mock_checkbox.setChecked(self.settings.mock_mode)
        self.mock_checkbox.stateChanged.connect(self._settings_changed)

        self.push_grade_checkbox = QtWidgets.QCheckBox("推送坡度到骑行台")
        self.push_grade_checkbox.setChecked(self.settings.push_grade)
        self.push_grade_checkbox.stateChanged.connect(self._settings_changed)

        exam_layout.addWidget(QtWidgets.QLabel("模式"), 0, 0)
        exam_layout.addWidget(self.exam_mode_combo, 0, 1)
        exam_layout.addWidget(QtWidgets.QLabel("时长"), 0, 2)
        exam_layout.addWidget(self.duration_combo, 0, 3)
        exam_layout.addWidget(self.custom_seconds, 0, 4)
        exam_layout.addWidget(QtWidgets.QLabel("整车自重"), 1, 0)
        exam_layout.addWidget(self.bike_weight_spin, 1, 1)
        exam_layout.addWidget(self.mock_checkbox, 1, 2)
        exam_layout.addWidget(self.push_grade_checkbox, 1, 3, 1, 2)
        exam_layout.setColumnStretch(5, 1)

        device_box = QtWidgets.QGroupBox("设备与选手")
        device_layout = QtWidgets.QVBoxLayout(device_box)
        device_buttons = QtWidgets.QHBoxLayout()
        self.scan_button = QtWidgets.QPushButton("扫描/绑定")
        self.scan_button.clicked.connect(self._open_scan_dialog)
        self.connect_button = QtWidgets.QPushButton("连接设备")
        self.connect_button.clicked.connect(self._connect_devices)
        device_buttons.addWidget(self.scan_button)
        device_buttons.addWidget(self.connect_button)
        device_buttons.addStretch(1)

        self.rider_settings_table = QtWidgets.QTableWidget(4, 5)
        self.rider_settings_table.setHorizontalHeaderLabels(
            ["分屏", "选手名", "体重 kg", "设备", "地址"]
        )
        self.rider_settings_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.rider_settings_table.verticalHeader().setVisible(False)
        self.rider_settings_table.itemChanged.connect(self._rider_settings_item_changed)

        device_layout.addLayout(device_buttons)
        device_layout.addWidget(self.rider_settings_table)

        layout.addWidget(exam_box)
        layout.addWidget(device_box, 1)
        self._apply_settings_to_widgets()
        return page

    def _build_log_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setObjectName("logOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(1200)
        layout.addWidget(self.log_output)
        return page

    def _stylesheet(self) -> str:
        return """
        QWidget {
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            font-size: 13px;
            color: #172026;
            background: #f4f6f8;
        }
        QTabWidget::pane {
            border: 1px solid #d9e0e7;
            background: #f4f6f8;
        }
        QGroupBox {
            background: #ffffff;
            border: 1px solid #d9e0e7;
            border-radius: 6px;
            margin-top: 10px;
            padding: 10px;
            font-weight: 700;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }
        QTableWidget {
            background: #ffffff;
            border: 1px solid #d9e0e7;
            border-radius: 6px;
            gridline-color: #e8eef2;
        }
        #controlBar, #riderPanel {
            background: #ffffff;
            border: 1px solid #d9e0e7;
            border-radius: 6px;
        }
        #panelTitle {
            font-size: 19px;
            font-weight: 700;
        }
        #riderNameLabel {
            font-size: 16px;
            font-weight: 700;
            color: #172026;
        }
        #riderWeightLabel {
            color: #64727d;
            font-weight: 600;
        }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            min-height: 28px;
            padding: 1px 6px;
            background: #ffffff;
            border: 1px solid #bac6d0;
            border-radius: 4px;
        }
        QPushButton {
            min-height: 30px;
            padding: 0 10px;
            background: #245b73;
            color: white;
            border: 0;
            border-radius: 4px;
            font-weight: 600;
        }
        QPushButton:disabled {
            background: #9cadb7;
            color: #edf2f4;
        }
        QPushButton:hover:!disabled {
            background: #1d4d62;
        }
        #primaryMetric {
            font-size: 42px;
            font-weight: 800;
            color: #0c3f4d;
        }
        #metricCaption {
            color: #64727d;
            font-size: 12px;
        }
        #fieldLabel {
            color: #64727d;
            font-size: 12px;
        }
        #fieldValue {
            font-weight: 600;
        }
        #routeProgress {
            min-height: 16px;
            max-height: 18px;
            border: 1px solid #c7d3dc;
            border-radius: 4px;
            background: #edf2f4;
            text-align: center;
            font-size: 11px;
            font-weight: 700;
        }
        #routeProgress::chunk {
            background: #74b72e;
            border-radius: 3px;
        }
        #statusDot {
            min-width: 14px;
        }
        QLabel[status="数据正常"] {
            color: #147a3d;
        }
        QLabel[status="已连接"] {
            color: #2d6380;
        }
        QLabel[status="连接中"] {
            color: #8a5a00;
        }
        QLabel[status="掉线"], QLabel[status="不支持"] {
            color: #b3261e;
        }
        #logOutput {
            background: #101820;
            color: #e7edf2;
            border: 1px solid #273642;
            border-radius: 6px;
            font-family: Consolas, "Microsoft YaHei Mono", monospace;
            font-size: 12px;
        }
        """

    def _duration_changed(self) -> None:
        self.custom_seconds.setVisible(self.duration_combo.currentData() == -1)
        self._settings_changed()

    def _selected_duration(self) -> int:
        value = int(self.duration_combo.currentData())
        return int(self.custom_seconds.value()) if value == -1 else value

    def _apply_settings_to_widgets(self) -> None:
        self._applying_settings_widgets = True
        mode_index = self.exam_mode_combo.findData(self.settings.exam_mode)
        self.exam_mode_combo.setCurrentIndex(max(0, mode_index))

        duration_index = self.duration_combo.findData(self.settings.duration_seconds)
        if duration_index >= 0:
            self.duration_combo.setCurrentIndex(duration_index)
        else:
            custom_index = self.duration_combo.findData(-1)
            self.duration_combo.setCurrentIndex(custom_index)
            self.custom_seconds.setValue(self.settings.duration_seconds)
        self.custom_seconds.setVisible(self.duration_combo.currentData() == -1)
        self.bike_weight_spin.setValue(self.settings.bike_weight_kg)
        self.mock_checkbox.setChecked(self.settings.mock_mode)
        self.push_grade_checkbox.setChecked(self.settings.push_grade)
        duration_enabled = self.settings.exam_mode == EXAM_MODE_TIME
        self.duration_combo.setEnabled(duration_enabled)
        self.custom_seconds.setEnabled(duration_enabled)
        self._update_mode_status_label()
        self._applying_settings_widgets = False

    def _settings_changed(self) -> None:
        if not hasattr(self, "exam_mode_combo"):
            return
        if self._applying_settings_widgets:
            return
        self.settings = AppSettings(
            exam_mode=str(self.exam_mode_combo.currentData() or EXAM_MODE_TIME),
            duration_seconds=self._selected_duration(),
            bike_weight_kg=float(self.bike_weight_spin.value()),
            mock_mode=self.mock_checkbox.isChecked(),
            push_grade=self.push_grade_checkbox.isChecked(),
        )
        self.controller.set_exam_mode(self.settings.exam_mode)
        self.controller.set_duration(self.settings.duration_seconds)
        self.controller.set_bike_weight(self.settings.bike_weight_kg)
        save_settings(self.settings)
        duration_enabled = self.settings.exam_mode == EXAM_MODE_TIME
        self.duration_combo.setEnabled(duration_enabled)
        self.custom_seconds.setEnabled(duration_enabled)
        if hasattr(self, "route_total_label"):
            self._update_route_total_label()
        self._update_mode_status_label()
        self._refresh_all_panels()

    def _update_mode_status_label(self) -> None:
        if not hasattr(self, "mode_status_label"):
            return
        if self.settings.exam_mode == EXAM_MODE_ROUTE:
            text = f"固定线路 | {self.route_profile.total_distance_m:.0f} m | 车重 {self.settings.bike_weight_kg:.1f} kg"
        else:
            text = f"固定时长 | {self.settings.duration_seconds} 秒 | 车重 {self.settings.bike_weight_kg:.1f} kg"
        self.mode_status_label.setText(text)

    def _populate_rider_settings_table(self) -> None:
        if not hasattr(self, "rider_settings_table"):
            return
        self._settings_table_blocked = True
        for row, rider in enumerate(self.controller.riders):
            values = [
                f"{rider.slot}号",
                rider.rider_name,
                f"{rider.weight_kg:.1f}",
                rider.device_name,
                rider.device_address,
            ]
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column in {0, 3, 4}:
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.rider_settings_table.setItem(row, column, item)
        self._settings_table_blocked = False

    def _rider_settings_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._settings_table_blocked:
            return
        slot = item.row() + 1
        if item.column() == 1:
            self.controller.set_rider_name(slot, item.text())
        elif item.column() == 2:
            try:
                self.controller.set_rider_weight(slot, float(item.text()))
            except ValueError:
                self._populate_rider_settings_table()
                return
        else:
            return
        self._save_config()
        self._refresh_panel(slot)

    def _load_config(self) -> None:
        try:
            if self.config_path.exists():
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.controller.load_bindings(list(data.get("slots", []) or []))
                self._log("已加载设备绑定配置")
        except Exception as exc:
            self._log(f"加载配置失败: {exc}")

    def _save_config(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(
                    self.controller.bindings_to_config(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            self._log(f"保存配置失败: {exc}")

    def _populate_route_table(self) -> None:
        self.route_table.setRowCount(0)
        for segment in self.route_profile.segments:
            self._insert_route_row(segment.distance_m, segment.grade_percent)
        self.route_profile_widget.set_route(self.route_profile)
        self.exam_route_profile_widget.set_route(self.route_profile)
        self._update_route_total_label()

    def _insert_route_row(self, distance_m: float, grade_percent: float) -> None:
        row = self.route_table.rowCount()
        self.route_table.insertRow(row)
        self.route_table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{distance_m:.1f}"))
        self.route_table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{grade_percent:.1f}"))

    def _add_route_segment(self) -> None:
        if self.controller.running:
            self._log("考试进行中不能修改赛道")
            return
        self._insert_route_row(300.0, 0.0)
        self._update_route_total_label()

    def _remove_route_segment(self) -> None:
        if self.controller.running:
            self._log("考试进行中不能修改赛道")
            return
        row = self.route_table.currentRow()
        if row < 0:
            row = self.route_table.rowCount() - 1
        if row >= 0:
            self.route_table.removeRow(row)
        if self.route_table.rowCount() == 0:
            self._insert_route_row(300.0, 0.0)
        self._update_route_total_label()

    def _move_route_segment(self, delta: int) -> None:
        if self.controller.running:
            self._log("考试进行中不能修改赛道")
            return
        row = self.route_table.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.route_table.rowCount():
            return

        rows: list[tuple[str, str]] = []
        for index in range(self.route_table.rowCount()):
            distance_item = self.route_table.item(index, 0)
            grade_item = self.route_table.item(index, 1)
            rows.append(
                (
                    distance_item.text() if distance_item else "300.0",
                    grade_item.text() if grade_item else "0.0",
                )
            )
        rows[row], rows[target] = rows[target], rows[row]

        self.route_table.setRowCount(0)
        for distance_text, grade_text in rows:
            insert_row = self.route_table.rowCount()
            self.route_table.insertRow(insert_row)
            self.route_table.setItem(insert_row, 0, QtWidgets.QTableWidgetItem(distance_text))
            self.route_table.setItem(insert_row, 1, QtWidgets.QTableWidgetItem(grade_text))
        self.route_table.setCurrentCell(target, 0)
        self._update_route_total_label()

    def _route_from_table(self) -> RouteProfile:
        segments: list[RouteSegment] = []
        for row in range(self.route_table.rowCount()):
            distance_item = self.route_table.item(row, 0)
            grade_item = self.route_table.item(row, 1)
            distance_m = float(distance_item.text()) if distance_item else 300.0
            grade_percent = float(grade_item.text()) if grade_item else 0.0
            segments.append(
                RouteSegment(
                    max(1.0, distance_m),
                    min(25.0, max(-20.0, grade_percent)),
                )
            )
        return RouteProfile(segments)

    def _apply_route_from_table(self) -> None:
        if self.controller.running:
            self._log("考试进行中不能修改赛道")
            return
        try:
            self.route_profile = self._route_from_table()
            self.controller.set_route(self.route_profile)
            self.route_profile_widget.set_route(self.route_profile)
            self.exam_route_profile_widget.set_route(self.route_profile)
            self._update_route_total_label()
            self._update_mode_status_label()
            self._refresh_all_panels()
            self._log("赛道已应用")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "赛道错误", str(exc))
            self._log(f"赛道应用失败: {exc}")

    def _save_route_from_table(self) -> None:
        if self.controller.running:
            self._log("考试进行中不能保存赛道")
            return
        try:
            self.route_profile = self._route_from_table()
            save_route(self.route_profile)
            self.controller.set_route(self.route_profile)
            self.route_profile_widget.set_route(self.route_profile)
            self.exam_route_profile_widget.set_route(self.route_profile)
            self._update_route_total_label()
            self._update_mode_status_label()
            self._log("赛道已保存")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "保存失败", str(exc))
            self._log(f"赛道保存失败: {exc}")

    def _update_route_total_label(self) -> None:
        try:
            total = self._route_from_table().total_distance_m
        except Exception:
            total = self.route_profile.total_distance_m
        suffix = "固定终点" if self.settings.exam_mode == EXAM_MODE_ROUTE else "循环使用"
        self.route_total_label.setText(f"总长 {total:.0f} m，{suffix}")
        self._update_mode_status_label()

    def _set_route_controls_enabled(self, enabled: bool) -> None:
        for widget in [
            self.route_table,
            self.add_segment_button,
            self.remove_segment_button,
            self.move_segment_up_button,
            self.move_segment_down_button,
            self.apply_route_button,
            self.save_route_button,
        ]:
            widget.setEnabled(enabled)

    def _set_settings_controls_enabled(self, enabled: bool) -> None:
        for widget in [
            self.exam_mode_combo,
            self.duration_combo,
            self.custom_seconds,
            self.bike_weight_spin,
            self.mock_checkbox,
            self.push_grade_checkbox,
            self.rider_settings_table,
            self.scan_button,
            self.connect_button,
        ]:
            widget.setEnabled(enabled)
        if enabled and self.settings.exam_mode == EXAM_MODE_ROUTE:
            self.duration_combo.setEnabled(False)
            self.custom_seconds.setEnabled(False)

    def _open_scan_dialog(self) -> None:
        if self.controller.running:
            QtWidgets.QMessageBox.information(self, "提示", "考试进行中不能重新绑定设备")
            return

        dialog = ScanDialog(self)
        dialog.device_selected.connect(self._bind_device)
        dialog.log_message.connect(self._log)
        dialog.exec()

    def _bind_device(self, slot: int, device: dict) -> None:
        if self.controller.running:
            self._log("考试进行中，已拒绝重新绑定")
            return
        self.controller.bind_device(slot, device)
        self._save_config()
        self._populate_rider_settings_table()
        self._refresh_panel(slot)
        self._log(f"{slot}号分屏已绑定 {device.get('name') or device.get('address')}")

    def _connect_devices(self) -> None:
        self._sync_rider_inputs()
        self._save_config()

        if self.ble_runtime and self.ble_runtime.isRunning():
            self.ble_runtime.stop()
            self.ble_runtime.wait(3000)

        if self.mock_checkbox.isChecked():
            for slot in range(1, 5):
                rider = self.controller.rider(slot)
                self.controller.bind_device(
                    slot,
                    {
                        "name": f"Mock Trainer {slot}",
                        "address": f"MOCK-{slot:02d}",
                        "service_uuids": ["mock"],
                    },
                )
                self.controller.rider(slot).weight_kg = rider.weight_kg
            self._populate_rider_settings_table()

        for rider in self.controller.riders:
            if rider.device_address:
                rider.connection_status = STATUS_CONNECTING
        self._refresh_all_panels()

        bindings = list(self.controller.bindings_to_config()["slots"])
        self.ble_runtime = BleRuntime(bindings, mock=self.mock_checkbox.isChecked())
        self.ble_runtime.power_received.connect(self._handle_power)
        self.ble_runtime.status_changed.connect(self._handle_status)
        self.ble_runtime.log_message.connect(self._log)
        self.ble_runtime.finished.connect(lambda: self._log("BLE 采集线程已停止"))
        self.ble_runtime.start()
        self._log("正在连接设备")

    def _prepare_exam(self) -> None:
        self._sync_rider_inputs()
        self._settings_changed()
        self.controller.set_duration(self._selected_duration())
        ok, message = self.controller.prepare()
        self.start_button.setEnabled(ok)
        self._log(message)

    def _start_exam(self) -> None:
        self._sync_rider_inputs()
        self._settings_changed()
        self.controller.set_duration(self._selected_duration())
        ok, message = self.controller.start()
        if not ok:
            self._log(message)
            return
        self.scan_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.prepare_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self._set_route_controls_enabled(False)
        self._set_settings_controls_enabled(False)
        for panel in self.panels.values():
            panel.set_inputs_locked(True)
        self._log(message)

    def _terminate_exam(self) -> None:
        ok, message = self.controller.terminate()
        self._log(message)
        if ok:
            self._exam_finished()

    def _reset_exam(self) -> None:
        self.controller.reset_exam()
        self.controller.set_route(self.route_profile)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.scan_button.setEnabled(True)
        self.connect_button.setEnabled(True)
        self.prepare_button.setEnabled(True)
        self._set_route_controls_enabled(True)
        self._set_settings_controls_enabled(True)
        for panel in self.panels.values():
            panel.set_inputs_locked(True)
        self._refresh_all_panels()
        self._log("考试数据已重置")

    def _export_csv(self) -> None:
        try:
            target_dir = export_exam_csv(self.controller)
            self._log(f"CSV 已导出: {target_dir}")
            QtWidgets.QMessageBox.information(self, "导出完成", f"CSV 已导出到:\n{target_dir}")
        except Exception as exc:
            self._log(f"导出失败: {exc}")
            QtWidgets.QMessageBox.warning(self, "导出失败", str(exc))

    def _handle_power(self, slot: int, power: int, timestamp: float) -> None:
        self.controller.update_power(slot, power, timestamp)
        self._refresh_panel(slot)

    def _handle_status(self, slot: int, status: str, message: str) -> None:
        self.controller.update_status(slot, status, message, time.time())
        self._refresh_panel(slot)
        if message:
            self._log(f"[{slot}号] {message}")

    def _on_timer(self) -> None:
        finished = self.controller.tick()
        if self.controller.running:
            self._push_route_grades()
        self._refresh_all_panels()
        if finished:
            self._log("考试时间到，成绩已锁定")
            self._exam_finished()

    def _push_route_grades(self) -> None:
        if not self.push_grade_checkbox.isChecked():
            return
        if not self.ble_runtime or not self.ble_runtime.isRunning():
            return
        now = time.monotonic()
        if now - self._last_grade_push_at < 1.0:
            return
        self._last_grade_push_at = now
        for rider in self.controller.riders:
            self.ble_runtime.set_grade(rider.slot, rider.current_grade_percent)

    def _exam_finished(self) -> None:
        self.scan_button.setEnabled(True)
        self.connect_button.setEnabled(True)
        self.prepare_button.setEnabled(True)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.export_button.setEnabled(True)
        self._set_route_controls_enabled(True)
        self._set_settings_controls_enabled(True)
        for panel in self.panels.values():
            panel.set_inputs_locked(True)
        self._refresh_all_panels()

    def _rider_name_changed(self, slot: int, name: str) -> None:
        self.controller.set_rider_name(slot, name)
        self._save_config()

    def _rider_weight_changed(self, slot: int, weight_kg: float) -> None:
        self.controller.set_rider_weight(slot, weight_kg)
        self._save_config()
        self._refresh_panel(slot)

    def _sync_rider_inputs(self) -> None:
        if hasattr(self, "rider_settings_table"):
            for row in range(self.rider_settings_table.rowCount()):
                slot = row + 1
                name_item = self.rider_settings_table.item(row, 1)
                weight_item = self.rider_settings_table.item(row, 2)
                if name_item:
                    self.controller.set_rider_name(slot, name_item.text())
                if weight_item:
                    try:
                        self.controller.set_rider_weight(slot, float(weight_item.text()))
                    except ValueError:
                        pass
        self._save_config()

    def _refresh_panel(self, slot: int) -> None:
        rider = self.controller.rider(slot)
        now = time.time()
        elapsed = rider.elapsed_at(now) if rider.start_time is not None else self.controller.current_elapsed(now)
        self.panels[slot].update_from_rider(
            rider,
            elapsed,
            self.route_profile.total_distance_m,
            now,
            self.settings.exam_mode,
        )

    def _refresh_all_panels(self) -> None:
        for slot in range(1, 5):
            self._refresh_panel(slot)
        if hasattr(self, "route_profile_widget"):
            distances = {rider.slot: rider.simulated_distance_m for rider in self.controller.riders}
            self.route_profile_widget.set_rider_distances(distances)
            self.exam_route_profile_widget.set_rider_distances(distances)
            self._update_exam_route_visibility()

    def _update_exam_route_visibility(self) -> None:
        if not hasattr(self, "exam_route_profile_widget"):
            return
        self.exam_route_profile_widget.setVisible(self.width() >= 980 and self.height() >= 740)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_exam_route_visibility()

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")
        self.logger.info(message)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.ble_runtime and self.ble_runtime.isRunning():
            self.ble_runtime.stop()
            self.ble_runtime.wait(3000)
        event.accept()
