from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from app.core.rider_state import RiderState

SLOT_COLORS = {
    1: "#dc3b35",
    2: "#2f80ed",
    3: "#8e44ad",
    4: "#f39c12",
}


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
        self.setMinimumSize(330, 210)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        title = QtWidgets.QLabel(f"{slot}号")
        title.setObjectName("panelTitle")
        title.setStyleSheet(f"color: {SLOT_COLORS.get(slot, '#245b73')};")

        self.name_label = QtWidgets.QLabel("选手")
        self.name_label.setObjectName("riderNameLabel")
        self.weight_label = QtWidgets.QLabel("70.0 kg")
        self.weight_label.setObjectName("riderWeightLabel")

        header = QtWidgets.QGridLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setHorizontalSpacing(8)
        header.addWidget(title, 0, 0)
        header.addWidget(self.name_label, 0, 1)
        header.addWidget(self.weight_label, 0, 2)
        header.setColumnStretch(1, 1)

        self.speed_label = QtWidgets.QLabel("--")
        self.speed_label.setObjectName("primaryMetric")
        self.power_label = QtWidgets.QLabel("--")
        self.power_label.setObjectName("primaryMetric")
        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setObjectName("statusDot")

        self.speed_caption = QtWidgets.QLabel("模拟速度 km/h")
        self.power_caption = QtWidgets.QLabel("功率 W")
        self.speed_caption.setObjectName("metricCaption")
        self.power_caption.setObjectName("metricCaption")

        metric_grid = QtWidgets.QGridLayout()
        metric_grid.setHorizontalSpacing(14)
        metric_grid.setVerticalSpacing(0)
        metric_grid.addWidget(self.speed_label, 0, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        power_wrap = QtWidgets.QWidget()
        power_wrap_layout = QtWidgets.QHBoxLayout(power_wrap)
        power_wrap_layout.setContentsMargins(0, 0, 0, 0)
        power_wrap_layout.setSpacing(8)
        power_wrap_layout.addStretch(1)
        power_wrap_layout.addWidget(self.power_label)
        power_wrap_layout.addWidget(self.status_dot)
        power_wrap_layout.addStretch(1)
        metric_grid.addWidget(power_wrap, 0, 1)
        metric_grid.addWidget(self.speed_caption, 1, 0, QtCore.Qt.AlignmentFlag.AlignCenter)
        metric_grid.addWidget(self.power_caption, 1, 1, QtCore.Qt.AlignmentFlag.AlignCenter)
        metric_grid.setColumnStretch(0, 1)
        metric_grid.setColumnStretch(1, 1)

        self.route_progress = QtWidgets.QProgressBar()
        self.route_progress.setRange(0, 1000)
        self.route_progress.setValue(0)
        self.route_progress.setFormat("0%")
        self.route_progress.setTextVisible(True)
        self.route_progress.setObjectName("routeProgress")

        self.hr_label = QtWidgets.QLabel("-- bpm")
        self.avg_power_label = QtWidgets.QLabel("-- W")
        self.avg_hr_label = QtWidgets.QLabel("-- bpm")
        self.grade_label = QtWidgets.QLabel("0.0%")
        self.elapsed_label = QtWidgets.QLabel("00:00")
        self.dropout_label = QtWidgets.QLabel("0.0 s")
        self.distance_label = QtWidgets.QLabel("0 m")
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
        self._add_value(detail_grid, 3, 1, "成绩", self.final_label)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addLayout(metric_grid)
        layout.addWidget(self.route_progress)
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
        self.name_label.setText(name or "选手")

    def set_weight(self, weight_kg: float) -> None:
        self.weight_label.setText(f"{weight_kg:.1f} kg")

    def set_inputs_locked(self, locked: bool) -> None:
        _ = locked

    def update_from_rider(
        self,
        rider: RiderState,
        elapsed: float,
        route_distance_m: float,
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
        progress = 0.0
        if route_distance_m > 0:
            progress = min(1.0, max(0.0, rider.simulated_distance_m / route_distance_m))
        self.route_progress.setValue(int(progress * 1000))
        self.route_progress.setFormat(f"{progress * 100:.0f}%")
        self._update_status_dot(rider.connection_status)

        if rider.final_status == "completed":
            self.final_label.setText(
                f"{rider.metrics.average_power:.1f} W / {max_power or 0} W max"
            )
        elif rider.final_status == "aborted":
            self.final_label.setText(f"{rider.metrics.average_power:.1f} W aborted")
        else:
            self.final_label.setText("-")

    def _update_status_dot(self, status: str) -> None:
        if status in {"数据正常", "已连接"}:
            color = "#147a3d"
        elif status == "连接中":
            color = "#8a5a00"
        else:
            color = "#b3261e"
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: 800;")
