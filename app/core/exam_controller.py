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
    STATUS_DROPPED,
    STATUS_UNSUPPORTED,
)
from app.core.simulation import (
    DRAFT_EFFECTIVE_GAP_M,
    draft_aero_multiplier,
    estimate_draft_savings_watts,
)

EXAM_MODE_TIME = "time"
EXAM_MODE_ROUTE = "route"


class ExamController:
    def __init__(self, duration_seconds: int = 60) -> None:
        self.duration_seconds = duration_seconds
        self.exam_mode = EXAM_MODE_TIME
        self.drafting_enabled = False
        self.bike_weight_kg = 10.0
        self.riders = [RiderState(slot=index) for index in range(1, 5)]
        self.route_profile = RouteProfile()
        self.active_slots: set[int] = set()
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

    def set_exam_mode(self, exam_mode: str) -> None:
        self.exam_mode = EXAM_MODE_ROUTE if exam_mode == EXAM_MODE_ROUTE else EXAM_MODE_TIME

    def set_drafting_enabled(self, enabled: bool) -> None:
        self.drafting_enabled = bool(enabled)

    def set_bike_weight(self, bike_weight_kg: float) -> None:
        self.bike_weight_kg = min(30.0, max(5.0, float(bike_weight_kg)))
        now = time.time()
        for rider in self.riders:
            rider.advance_simulation(
                now,
                self.route_profile,
                bike_weight_kg=self.bike_weight_kg,
                loop_route=self.exam_mode == EXAM_MODE_TIME,
            )

    def set_route(self, route_profile: RouteProfile) -> None:
        self.route_profile = route_profile
        now = time.time()
        for rider in self.riders:
            rider.advance_simulation(
                now,
                self.route_profile,
                bike_weight_kg=self.bike_weight_kg,
                loop_route=self.exam_mode == EXAM_MODE_TIME,
            )

    def set_rider_name(self, slot: int, name: str) -> None:
        self.rider(slot).rider_name = name

    def set_rider_weight(self, slot: int, weight_kg: float) -> None:
        rider = self.rider(slot)
        rider.weight_kg = min(200.0, max(30.0, float(weight_kg)))
        rider.advance_simulation(
            time.time(),
            self.route_profile,
            bike_weight_kg=self.bike_weight_kg,
            loop_route=self.exam_mode == EXAM_MODE_TIME,
        )

    def bind_device(self, slot: int, device: dict[str, object]) -> None:
        rider = self.rider(slot)
        rider.device_name = str(device.get("name") or device.get("device_name") or "")
        rider.device_address = str(device.get("address") or device.get("device_address") or "")
        rider.service_uuids = list(device.get("service_uuids", []) or [])
        rider.connection_status = STATUS_DISCONNECTED
        rider.connection_message = "设备已绑定"

    def rider(self, slot: int) -> RiderState:
        return self.riders[slot - 1]

    def active_riders(self) -> list[RiderState]:
        return [r for r in self.riders if r.device_address or r.device_name]

    def prepare(self) -> tuple[bool, str]:
        active_riders = self.active_riders()
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
        self.active_slots = {rider.slot for rider in self.active_riders()}
        self.samples.clear()
        for rider in self.riders:
            if rider.slot in self.active_slots:
                rider.begin_exam(now)
                self._record_sample(rider, now)
            else:
                rider.reset_exam()
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
        self.active_slots.clear()
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
        if rider.final_status == "completed":
            return
        self._advance_rider_simulation(
            rider,
            now,
        )
        accepted = rider.add_power(now, power)
        self._advance_rider_simulation(
            rider,
            now,
        )
        if self.running and accepted:
            self._record_sample(rider, now)

    def tick(self, now: float | None = None) -> bool:
        if not self.running:
            return False

        current = now or time.time()
        elapsed = current - (self.start_time or current)
        for rider in self.riders:
            if rider.slot not in self.active_slots or not rider.exam_running:
                continue
            route_finished = self._advance_rider_simulation(
                rider,
                current,
            )
            rider.check_dropout(current)
            second = int(elapsed)
            if second != rider.last_periodic_second:
                rider.last_periodic_second = second
                self._record_sample(rider, current)
            if route_finished and self.exam_mode == EXAM_MODE_ROUTE:
                self._finish_rider(rider, rider.finish_crossed_at or current, "completed")

        if self.exam_mode == EXAM_MODE_TIME and elapsed >= self.duration_seconds:
            self._finish(aborted=False, end_time=(self.start_time or current) + self.duration_seconds)
            return True
        if self.exam_mode == EXAM_MODE_ROUTE and self._all_active_riders_finished():
            self.running = False
            self.locked = True
            self.ready = False
            self.end_time = current
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
                self.exam_mode,
                self.drafting_enabled,
                self._finish_distance_m() or self.route_profile.total_distance_m,
                self.bike_weight_kg,
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
            if rider.slot not in self.active_slots:
                continue
            if rider.final_status == "completed" and aborted:
                continue
            if rider.exam_running:
                self._advance_rider_simulation(
                    rider,
                    finish_time,
                )
                rider.finish_exam(finish_time, status)
                self._record_sample(rider, finish_time)

    def _finish_rider(self, rider: RiderState, finish_time: float, status: str) -> None:
        if not rider.exam_running:
            return
        rider.finish_exam(finish_time, status)
        self._record_sample(rider, finish_time)

    def _all_active_riders_finished(self) -> bool:
        if not self.active_slots:
            return False
        return all(not self.rider(slot).exam_running for slot in self.active_slots)

    def _finish_distance_m(self) -> float | None:
        if self.exam_mode != EXAM_MODE_ROUTE:
            return None
        total = self.route_profile.total_distance_m
        return total if total > 0 else None

    def _advance_rider_simulation(self, rider: RiderState, now: float) -> bool:
        multiplier, gap_m, leader_slot, riders_ahead, savings_watts = self._draft_effect_for(rider)
        return rider.advance_simulation(
            now,
            self.route_profile,
            bike_weight_kg=self.bike_weight_kg,
            loop_route=self.exam_mode == EXAM_MODE_TIME,
            finish_distance_m=self._finish_distance_m(),
            draft_aero_multiplier=multiplier,
            draft_gap_m=gap_m,
            draft_leader_slot=leader_slot,
            draft_riders_ahead=riders_ahead,
            draft_savings_watts=savings_watts,
        )

    def _draft_effect_for(
        self,
        rider: RiderState,
    ) -> tuple[float, float | None, int | None, int, float]:
        if not self.drafting_enabled or self.exam_mode != EXAM_MODE_ROUTE:
            return 1.0, None, None, 0, 0.0
        if not rider.exam_running or rider.connection_status == STATUS_DROPPED:
            return 1.0, None, None, 0, 0.0

        nearest_gap: float | None = None
        leader_slot: int | None = None
        riders_ahead = 0
        for other in self.riders:
            if other.slot == rider.slot:
                continue
            if other.slot not in self.active_slots or not other.exam_running:
                continue
            if other.connection_status == STATUS_DROPPED:
                continue
            gap = other.simulated_distance_m - rider.simulated_distance_m
            if gap <= 0:
                continue
            if gap <= DRAFT_EFFECTIVE_GAP_M:
                riders_ahead += 1
            if nearest_gap is None or gap < nearest_gap:
                nearest_gap = gap
                leader_slot = other.slot

        if nearest_gap is None:
            return 1.0, None, None, 0, 0.0

        multiplier = draft_aero_multiplier(nearest_gap, rider.simulated_speed_mps, riders_ahead=riders_ahead)
        if multiplier >= 0.999:
            return 1.0, None, None, 0, 0.0
        savings_watts = estimate_draft_savings_watts(rider.simulated_speed_mps, multiplier)
        return multiplier, nearest_gap, leader_slot, riders_ahead, savings_watts

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
                segment_index=rider.current_segment_index,
                segment_progress=rider.current_segment_progress,
                draft_aero_multiplier=rider.draft_aero_multiplier,
                draft_gap_m=rider.draft_gap_m,
                draft_leader_slot=rider.draft_leader_slot,
                draft_riders_ahead=rider.draft_riders_ahead,
                draft_savings_watts=rider.draft_savings_watts,
                heart_rate_bpm=None
                if rider.heart_rate_metrics.current_value is None
                else int(rider.heart_rate_metrics.current_value),
                status=rider.connection_status,
            )
        )


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "devices.json"
