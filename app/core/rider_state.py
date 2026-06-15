from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.metrics import PowerMetrics, TimeWeightedMetric
from app.core.route import RouteProfile
from app.core.simulation import advance_speed_mps


STATUS_DISCONNECTED = "未连接"
STATUS_CONNECTING = "连接中"
STATUS_CONNECTED = "已连接"
STATUS_DATA_OK = "数据正常"
STATUS_DROPPED = "掉线"
STATUS_UNSUPPORTED = "不支持"

POWER_STALE_THRESHOLD_SECONDS = 3.0


@dataclass(slots=True)
class DeviceBinding:
    slot: int
    rider_name: str = ""
    device_name: str = ""
    device_address: str = ""
    service_uuids: list[str] = field(default_factory=list)
    weight_kg: float = 70.0

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceBinding":
        return cls(
            slot=int(data.get("slot", 0)),
            rider_name=str(data.get("rider_name", "")),
            device_name=str(data.get("device_name", "")),
            device_address=str(data.get("device_address", "")),
            service_uuids=list(data.get("service_uuids", []) or []),
            weight_kg=float(data.get("weight_kg", 70.0)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "rider_name": self.rider_name,
            "device_name": self.device_name,
            "device_address": self.device_address,
            "service_uuids": self.service_uuids,
            "weight_kg": round(self.weight_kg, 1),
        }


@dataclass(slots=True)
class SampleRecord:
    exam_id: str
    rider_slot: int
    timestamp: str
    elapsed_seconds: float
    current_power: int | None
    simulated_speed_kph: float
    simulated_distance_m: float
    grade_percent: float
    segment_index: int
    segment_progress: float
    heart_rate_bpm: int | None
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "exam_id": self.exam_id,
            "rider_slot": self.rider_slot,
            "timestamp": self.timestamp,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "current_power": "" if self.current_power is None else self.current_power,
            "simulated_speed_kph": round(self.simulated_speed_kph, 2),
            "simulated_distance_m": round(self.simulated_distance_m, 2),
            "grade_percent": round(self.grade_percent, 2),
            "segment_index": self.segment_index,
            "segment_progress": round(self.segment_progress, 4),
            "heart_rate_bpm": "" if self.heart_rate_bpm is None else self.heart_rate_bpm,
            "status": self.status,
        }


@dataclass
class RiderState:
    slot: int
    rider_name: str = ""
    device_name: str = ""
    device_address: str = ""
    service_uuids: list[str] = field(default_factory=list)
    weight_kg: float = 70.0
    connection_status: str = STATUS_DISCONNECTED
    connection_message: str = ""
    metrics: PowerMetrics = field(default_factory=PowerMetrics)
    heart_rate_metrics: TimeWeightedMetric = field(default_factory=TimeWeightedMetric)
    simulated_speed_kph: float = 0.0
    simulated_speed_mps: float = 0.0
    simulated_distance_m: float = 0.0
    current_grade_percent: float = 0.0
    current_segment_index: int = 1
    total_segments: int = 1
    current_segment_progress: float = 0.0
    current_segment_distance_m: float = 0.0
    current_segment_length_m: float = 1.0
    last_simulation_timestamp: float | None = None
    last_power_timestamp: float | None = None
    dropout_started_at: float | None = None
    dropout_total: float = 0.0
    exam_running: bool = False
    final_status: str = "not_started"
    start_time: float | None = None
    end_time: float | None = None
    finish_crossed_at: float | None = None
    last_periodic_second: int = -1

    def apply_binding(self, binding: DeviceBinding) -> None:
        self.rider_name = binding.rider_name
        self.device_name = binding.device_name
        self.device_address = binding.device_address
        self.service_uuids = list(binding.service_uuids)
        self.weight_kg = binding.weight_kg

    def to_binding(self) -> DeviceBinding:
        return DeviceBinding(
            slot=self.slot,
            rider_name=self.rider_name,
            device_name=self.device_name,
            device_address=self.device_address,
            service_uuids=list(self.service_uuids),
            weight_kg=self.weight_kg,
        )

    def reset_exam(self) -> None:
        self.metrics.reset()
        self.heart_rate_metrics.reset()
        self.simulated_speed_kph = 0.0
        self.simulated_speed_mps = 0.0
        self.simulated_distance_m = 0.0
        self.current_grade_percent = 0.0
        self.current_segment_index = 1
        self.total_segments = 1
        self.current_segment_progress = 0.0
        self.current_segment_distance_m = 0.0
        self.current_segment_length_m = 1.0
        self.last_simulation_timestamp = None
        self.last_power_timestamp = None
        self.dropout_started_at = None
        self.dropout_total = 0.0
        self.exam_running = False
        self.final_status = "not_started"
        self.start_time = None
        self.end_time = None
        self.finish_crossed_at = None
        self.last_periodic_second = -1

    def begin_exam(self, start_time: float) -> None:
        self.reset_exam()
        self.exam_running = True
        self.final_status = "running"
        self.start_time = start_time
        self.last_simulation_timestamp = start_time

    def finish_exam(self, end_time: float, status: str) -> None:
        self.check_dropout(end_time)
        self.metrics.finalize_until(end_time)
        self.heart_rate_metrics.finalize_until(end_time)
        if self.dropout_started_at is not None:
            self.dropout_total += max(0.0, end_time - self.dropout_started_at)
            self.dropout_started_at = None
        self.exam_running = False
        self.final_status = status
        self.end_time = end_time
        self.metrics.lock()
        self.heart_rate_metrics.lock()

    def advance_simulation(
        self,
        now: float,
        route: RouteProfile,
        bike_weight_kg: float = 10.0,
        loop_route: bool = True,
        finish_distance_m: float | None = None,
    ) -> bool:
        if self.final_status == "completed":
            return False
        if self.last_simulation_timestamp is None:
            self.last_simulation_timestamp = now

        dt = max(0.0, now - self.last_simulation_timestamp)
        if self.exam_running:
            self.check_dropout(now)

        self.current_grade_percent = route.grade_at(self.simulated_distance_m, loop=loop_route)
        previous_speed_mps = self.simulated_speed_mps
        next_speed_mps = advance_speed_mps(
            previous_speed_mps,
            self._simulation_power(now),
            self.weight_kg,
            bike_weight_kg,
            self.current_grade_percent,
            dt,
        )
        if self.exam_running and dt > 0:
            previous_distance_m = self.simulated_distance_m
            distance_delta_m = (previous_speed_mps + next_speed_mps) / 2.0 * dt
            self.simulated_distance_m += distance_delta_m
            if (
                finish_distance_m is not None
                and previous_distance_m < finish_distance_m <= self.simulated_distance_m
            ):
                if distance_delta_m > 0:
                    ratio = (finish_distance_m - previous_distance_m) / distance_delta_m
                    self.finish_crossed_at = self.last_simulation_timestamp + dt * ratio
                else:
                    self.finish_crossed_at = now
                self.simulated_distance_m = finish_distance_m

        self.simulated_speed_mps = next_speed_mps
        self.simulated_speed_kph = round(self.simulated_speed_mps * 3.6, 2)
        (
            self.current_segment_index,
            self.total_segments,
            self.current_segment_distance_m,
            self.current_segment_length_m,
            self.current_segment_progress,
        ) = route.segment_progress_at(self.simulated_distance_m, loop=loop_route)

        self.last_simulation_timestamp = now
        return finish_distance_m is not None and self.simulated_distance_m >= finish_distance_m

    def update_status(self, status: str, message: str, timestamp: float) -> None:
        self.connection_status = status
        self.connection_message = message
        if status in {STATUS_DISCONNECTED, STATUS_DROPPED, STATUS_UNSUPPORTED}:
            self.metrics.current_power = None
        if not self.exam_running:
            return

        if status == STATUS_DROPPED and self.dropout_started_at is None:
            self.dropout_started_at = timestamp
        elif status == STATUS_DATA_OK and self.dropout_started_at is not None:
            self.dropout_total += max(0.0, timestamp - self.dropout_started_at)
            self.dropout_started_at = None

    def add_power(self, timestamp: float, power: int) -> bool:
        self.connection_status = STATUS_DATA_OK
        self.last_power_timestamp = timestamp
        if self.dropout_started_at is not None:
            self.dropout_total += max(0.0, timestamp - self.dropout_started_at)
            self.dropout_started_at = None
        if not self.exam_running:
            self.metrics.current_power = power
            return False
        return self.metrics.add_power(timestamp, power)

    def check_dropout(self, now: float, threshold: float = 3.0) -> None:
        if not self.exam_running:
            return

        if self.last_power_timestamp is not None:
            timeout_at = self.last_power_timestamp + threshold
            if now > timeout_at and self.connection_status == STATUS_DATA_OK:
                self.connection_status = STATUS_DROPPED
                self.metrics.current_power = None
                if self.dropout_started_at is None:
                    self.dropout_started_at = timeout_at
            return

        if (
            self.start_time is not None
            and now - self.start_time > threshold
            and self.connection_status in {STATUS_CONNECTED, STATUS_DATA_OK}
        ):
            self.connection_status = STATUS_DROPPED
            self.metrics.current_power = None
            if self.dropout_started_at is None:
                self.dropout_started_at = self.start_time + threshold

    def _simulation_power(self, now: float) -> int | None:
        if self.metrics.current_power is None:
            return None
        if self.connection_status == STATUS_DROPPED:
            return None
        if self.last_power_timestamp is None:
            return self.metrics.current_power
        if now - self.last_power_timestamp > POWER_STALE_THRESHOLD_SECONDS:
            self.metrics.current_power = None
            return None
        return self.metrics.current_power

    def dropout_time_at(self, now: float | None = None) -> float:
        total = self.dropout_total
        if self.dropout_started_at is not None and now is not None:
            total += max(0.0, now - self.dropout_started_at)
        return total

    def elapsed_at(self, now: float | None = None) -> float:
        if self.start_time is None:
            return 0.0
        end = self.end_time if self.end_time is not None else now
        if end is None:
            return 0.0
        return max(0.0, end - self.start_time)

    def summary_row(
        self,
        exam_id: str,
        duration_seconds: int,
        exam_mode: str,
        route_distance_m: float,
        bike_weight_kg: float,
        global_start: float | None,
        global_end: float | None,
    ) -> dict[str, object]:
        end_time = self.end_time if self.end_time is not None else global_end
        elapsed = self.elapsed_at(end_time)
        route_result = exam_mode == "route" and self.final_status == "completed"
        return {
            "exam_id": exam_id,
            "rider_slot": self.slot,
            "rider_name": self.rider_name,
            "device_name": self.device_name,
            "device_address": self.device_address,
            "weight_kg": round(self.weight_kg, 1),
            "bike_weight_kg": round(bike_weight_kg, 1),
            "exam_mode": exam_mode,
            "duration_seconds": duration_seconds,
            "route_distance_m": round(route_distance_m, 2),
            "finish_time_seconds": round(elapsed, 3) if route_result else "",
            "average_power": round(self.metrics.average_power, 2),
            "max_power": "" if self.metrics.max_power is None else self.metrics.max_power,
            "average_heart_rate": ""
            if self.heart_rate_metrics.valid_time <= 0
            else round(self.heart_rate_metrics.average_value, 1),
            "max_heart_rate": "" if self.heart_rate_metrics.max_value is None else int(self.heart_rate_metrics.max_value),
            "simulated_distance_m": round(self.simulated_distance_m, 2),
            "average_speed_kph": round((self.simulated_distance_m / elapsed * 3.6), 2) if elapsed > 0 else 0.0,
            "valid_time": round(self.metrics.valid_time, 3),
            "dropout_time": round(self.dropout_time_at(end_time), 3),
            "status": self.final_status,
            "start_time": _format_timestamp(global_start),
            "end_time": _format_timestamp(end_time),
        }


def _format_timestamp(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
