from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from app.core.route import RouteProfile
from app.core.rider_state import (
    DeviceBinding,
    RiderState,
    SampleRecord,
    STATUS_CONNECTED,
    STATUS_DATA_OK,
    STATUS_DISCONNECTED,
    STATUS_UNSUPPORTED,
)


class ExamController:
    def __init__(self, duration_seconds: int = 60) -> None:
        self.duration_seconds = duration_seconds
        self.riders = [RiderState(slot=index) for index in range(1, 5)]
        self.route_profile = RouteProfile()
        self.exam_id = ""
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.running = False
        self.locked = False
        self.ready = False
        self.samples: list[SampleRecord] = []

    def load_bindings(self, slots: list[dict]) -> None:
        by_slot = {int(item.get("slot", 0)): item for item in slots}
        for rider in self.riders:
            data = by_slot.get(rider.slot)
            if data:
                rider.apply_binding(DeviceBinding.from_dict(data))

    def bindings_to_config(self) -> dict[str, object]:
        return {"slots": [rider.to_binding().to_dict() for rider in self.riders]}

    def set_duration(self, duration_seconds: int) -> None:
        self.duration_seconds = max(1, int(duration_seconds))

    def set_route(self, route_profile: RouteProfile) -> None:
        self.route_profile = route_profile
        now = time.time()
        for rider in self.riders:
            rider.advance_simulation(now, self.route_profile)

    def set_rider_name(self, slot: int, name: str) -> None:
        self.rider(slot).rider_name = name

    def set_rider_weight(self, slot: int, weight_kg: float) -> None:
        rider = self.rider(slot)
        rider.weight_kg = min(200.0, max(30.0, float(weight_kg)))
        rider.advance_simulation(time.time(), self.route_profile)

    def bind_device(self, slot: int, device: dict[str, object]) -> None:
        rider = self.rider(slot)
        rider.device_name = str(device.get("name") or device.get("device_name") or "")
        rider.device_address = str(device.get("address") or device.get("device_address") or "")
        rider.service_uuids = list(device.get("service_uuids", []) or [])
        rider.connection_status = STATUS_DISCONNECTED
        rider.connection_message = "设备已绑定"

    def rider(self, slot: int) -> RiderState:
        return self.riders[slot - 1]

    def prepare(self) -> tuple[bool, str]:
        active_riders = [r for r in self.riders if r.device_address or r.device_name]
        if not active_riders:
            return False, "请先绑定或连接至少一台设备"

        unsupported = [r.slot for r in active_riders if r.connection_status == STATUS_UNSUPPORTED]
        if unsupported:
            return False, f"{unsupported} 号分屏不支持功率读取"

        not_connected = [
            r.slot
            for r in active_riders
            if r.connection_status not in {STATUS_CONNECTED, STATUS_DATA_OK}
        ]
        if not_connected:
            return False, f"{not_connected} 号分屏尚未连接"

        self.ready = True
        return True, "准备完成，可以开始考试"

    def start(self) -> tuple[bool, str]:
        if self.running:
            return False, "考试已经在进行中"
        if not self.ready:
            ok, message = self.prepare()
            if not ok:
                return ok, message

        now = time.time()
        self.exam_id = datetime.fromtimestamp(now).strftime("exam_%Y%m%d_%H%M%S")
        self.start_time = now
        self.end_time = None
        self.running = True
        self.locked = False
        self.samples.clear()
        for rider in self.riders:
            rider.begin_exam(now)
            self._record_sample(rider, now)
        return True, "考试开始"

    def terminate(self) -> tuple[bool, str]:
        if not self.running:
            return False, "当前没有正在进行的考试"
        self._finish(aborted=True)
        return True, "考试已终止"

    def reset_exam(self) -> None:
        self.running = False
        self.locked = False
        self.ready = False
        self.exam_id = ""
        self.start_time = None
        self.end_time = None
        self.samples.clear()
        for rider in self.riders:
            rider.reset_exam()

    def update_status(
        self,
        slot: int,
        status: str,
        message: str = "",
        timestamp: float | None = None,
    ) -> None:
        self.rider(slot).update_status(status, message, timestamp or time.time())

    def update_power(self, slot: int, power: int, timestamp: float | None = None) -> None:
        now = timestamp or time.time()
        rider = self.rider(slot)
        rider.advance_simulation(now, self.route_profile)
        accepted = rider.add_power(now, power)
        rider.advance_simulation(now, self.route_profile)
        if self.running and accepted:
            self._record_sample(rider, now)

    def tick(self, now: float | None = None) -> bool:
        if not self.running:
            return False

        current = now or time.time()
        elapsed = current - (self.start_time or current)
        for rider in self.riders:
            rider.advance_simulation(current, self.route_profile)
            rider.check_dropout(current)
            second = int(elapsed)
            if second != rider.last_periodic_second:
                rider.last_periodic_second = second
                self._record_sample(rider, current)

        if elapsed >= self.duration_seconds:
            self._finish(aborted=False, end_time=(self.start_time or current) + self.duration_seconds)
            return True
        return False

    def current_elapsed(self, now: float | None = None) -> float:
        if self.start_time is None:
            return 0.0
        end = self.end_time if self.end_time is not None else (now or time.time())
        return max(0.0, end - self.start_time)

    def summary_rows(self) -> list[dict[str, object]]:
        return [
            rider.summary_row(
                self.exam_id,
                self.duration_seconds,
                self.start_time,
                self.end_time,
            )
            for rider in self.riders
        ]

    def sample_rows(self) -> list[dict[str, object]]:
        return [sample.to_dict() for sample in self.samples]

    def _finish(self, aborted: bool, end_time: float | None = None) -> None:
        finish_time = end_time or time.time()
        self.running = False
        self.locked = True
        self.ready = False
        self.end_time = finish_time
        status = "aborted" if aborted else "completed"
        for rider in self.riders:
            rider.advance_simulation(finish_time, self.route_profile)
            rider.finish_exam(finish_time, status)
            self._record_sample(rider, finish_time)

    def _record_sample(self, rider: RiderState, timestamp: float) -> None:
        if not self.exam_id or self.start_time is None:
            return
        self.samples.append(
            SampleRecord(
                exam_id=self.exam_id,
                rider_slot=rider.slot,
                timestamp=datetime.fromtimestamp(timestamp).isoformat(timespec="milliseconds"),
                elapsed_seconds=max(0.0, timestamp - self.start_time),
                current_power=rider.metrics.current_power,
                simulated_speed_kph=rider.simulated_speed_kph,
                simulated_distance_m=rider.simulated_distance_m,
                grade_percent=rider.current_grade_percent,
                heart_rate_bpm=None
                if rider.heart_rate_metrics.current_value is None
                else int(rider.heart_rate_metrics.current_value),
                status=rider.connection_status,
            )
        )


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "devices.json"
