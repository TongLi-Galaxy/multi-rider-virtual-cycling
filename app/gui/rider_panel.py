from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from app.core.rider_state import RiderState


def format_seconds(value: float) -> str:
    total = max(0, int(value))
    minutes, seconds = divmod(total, 60)
    return f"{minutes:02d}:{seconds:02d}"


class RiderPanel(QtWidgets.QFrame):
    rider_name_changed = QtCore.Signal(int, str)
    rider_weight_changed = QtCore.Signal(int, float)

    def __init__(self, slot: int, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.slot = slot
        self.setObjectName("riderPanel")
        self.setMinimumSize(330, 230)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        title = QtWidgets.QLabel(f"{slot}号")
        title.setObjectName("panelTitle")

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("选手名")
        self.name_edit.textChanged.connect(
            lambda text: self.rider_name_changed.emit(self.slot, text)
        )

        self.weight_spin = QtWidgets.QDoubleSpinBox()
        self.weight_spin.setRange(30.0, 200.0)
        self.weight_spin.setDecimals(1)
        self.weight_spin.setSingleStep(0.5)
        self.weight_spin.setSuffix(" kg")
        self.weight_spin.setValue(70.0)
        self.weight_spin.valueChanged.connect(
            lambda value: self.rider_weight_changed.emit(self.slot, float(value))
        )

        header = QtWidgets.QGridLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setHorizontalSpacing(8)
        header.addWidget(title, 0, 0)
        header.addWidget(self.name_edit, 0, 1)
        header.addWidget(self.weight_spin, 0, 2)
        header.setColumnStretch(1, 1)

        self.speed_label = QtWidgets.QLabel("--")
        self.speed_label.setObjectName("primaryMetric")
        self.power_label = QtWidgets.QLabel("--")
        self.power_label.setObjectName("primaryMetric")

        self.speed_caption = QtWidgets.QLabel("模拟速度 km/h")
        self.power_caption = QtWidgets.QLabel("功率 W")
        self.speed_caption.setObjectName("metricCaption")
        self.power_caption.setObjectName("metricCaption")

        metric_grid = QtWidgets.QGridLayout()
        metric_grid.setHorizontalSpacing(14)
        metric_grid.setVerticalSpacing(0)
        metric_grid.addWidget(self.speed_label, 0, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        metric_grid.addWidget(self.power_label, 0, 1, QtCore.Qt.AlignmentFlag.AlignCenter)
        metric_grid.addWidget(self.speed_caption, 1, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        metric_grid.addWidget(self.power_caption, 1, 1, QtCore.Qt.AlignmentFlag.AlignCenter)
        metric_grid.setColumnStretch(0, 1)
        metric_grid.setColumnStretch(1, 1)

        self.hr_label = QtWidgets.QLabel("-- bpm")
        self.avg_power_label = QtWidgets.QLabel("-- W")
        self.avg_hr_label = QtWidgets.QLabel("-- bpm")
        self.grade_label = QtWidgets.QLabel("0.0%")
        self.elapsed_label = QtWidgets.QLabel("00:00")
        self.dropout_label = QtWidgets.QLabel("0.0 s")
        self.distance_label = QtWidgets.QLabel("0 m")
        self.segment_label = QtWidgets.QLabel("1/1 0%")
        self.status_label = QtWidgets.QLabel("未连接")
        self.device_label = QtWidgets.QLabel("-")
        self.final_label = QtWidgets.QLabel("-")

        detail_grid = QtWidgets.QGridLayout()
        detail_grid.setHorizontalSpacing(12)
        detail_grid.setVerticalSpacing(5)
        self._add_value(detail_grid, 0, 0, "心率", self.hr_label)
        self._add_value(detail_grid, 0, 1, "平均功率", self.avg_power_label)
        self._add_value(detail_grid, 1, 0, "平均心率", self.avg_hr_label)
        self._add_value(detail_grid, 1, 1, "坡度", self.grade_label)
        self._add_value(detail_grid, 2, 0, "已用时间", self.elapsed_label)
        self._add_value(detail_grid, 2, 1, "掉线", self.dropout_label)
        self._add_value(detail_grid, 3, 0, "距离", self.distance_label)
        self._add_value(detail_grid, 3, 1, "赛段", self.segment_label)
        self._add_value(detail_grid, 4, 0, "状态", self.status_label)
        self._add_value(detail_grid, 4, 1, "成绩", self.final_label)
        self._add_value(detail_grid, 5, 0, "设备", self.device_label)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(metric_grid)
        layout.addLayout(detail_grid)

    def _add_value(
        self,
        grid: QtWidgets.QGridLayout,
        row: int,
        column: int,
        label_text: str,
        value_widget: QtWidgets.QLabel,
    ) -> None:
        wrapper = QtWidgets.QWidget()
        wrapper_layout = QtWidgets.QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(4)

        label = QtWidgets.QLabel(label_text)
        label.setObjectName("fieldLabel")
        value_widget.setObjectName("fieldValue")
        value_widget.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        value_widget.setMinimumWidth(78)

        wrapper_layout.addWidget(label)
        wrapper_layout.addStretch(1)
        wrapper_layout.addWidget(value_widget)
        grid.addWidget(wrapper, row, column)

    def set_rider_name(self, name: str) -> None:
        if self.name_edit.text() == name:
            return
        blocker = QtCore.QSignalBlocker(self.name_edit)
        self.name_edit.setText(name)
        del blocker

    def set_weight(self, weight_kg: float) -> None:
        if abs(self.weight_spin.value() - weight_kg) < 0.05:
            return
        blocker = QtCore.QSignalBlocker(self.weight_spin)
        self.weight_spin.setValue(weight_kg)
        del blocker

    def set_inputs_locked(self, locked: bool) -> None:
        self.name_edit.setEnabled(not locked)
        self.weight_spin.setEnabled(not locked)

    def update_from_rider(
        self,
        rider: RiderState,
        elapsed: float,
        now: float | None = None,
    ) -> None:
        self.set_rider_name(rider.rider_name)
        self.set_weight(rider.weight_kg)

        current = rider.metrics.current_power
        current_hr = rider.heart_rate_metrics.current_value
        max_power = rider.metrics.max_power
        speed = rider.simulated_speed_kph

        self.speed_label.setText(f"{speed:.1f}")
        self.power_label.setText("--" if current is None else str(current))
        self.hr_label.setText("-- bpm" if current_hr is None else f"{int(current_hr)} bpm")
        self.avg_power_label.setText(f"{rider.metrics.average_power:.1f} W")
        self.avg_hr_label.setText(f"{rider.heart_rate_metrics.average_value:.0f} bpm")
        self.grade_label.setText(f"{rider.current_grade_percent:.1f}%")
        self.elapsed_label.setText(format_seconds(elapsed))
        self.dropout_label.setText(f"{rider.dropout_time_at(now):.1f} s")
        self.distance_label.setText(f"{rider.simulated_distance_m:.0f} m")
        self.segment_label.setText(
            f"{rider.current_segment_index}/{rider.total_segments} "
            f"{rider.current_segment_progress * 100:.0f}%"
        )
        self.device_label.setText(rider.device_name or rider.device_address or "-")
        self.device_label.setToolTip(rider.device_address or rider.device_name)
        self.status_label.setText(rider.connection_status)
        self.status_label.setProperty("status", rider.connection_status)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        if rider.final_status == "completed":
            self.final_label.setText(
                f"{rider.metrics.average_power:.1f} W / {max_power or 0} W max"
            )
        elif rider.final_status == "aborted":
            self.final_label.setText(f"{rider.metrics.average_power:.1f} W aborted")
        else:
            self.final_label.setText("-")
