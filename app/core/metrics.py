from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PowerMetrics:
    dropout_threshold: float = 3.0
    current_power: int | None = None
    max_power: int | None = None
    power_time_integral: float = 0.0
    valid_time: float = 0.0
    last_power: int | None = None
    last_timestamp: float | None = None
    abnormal_count: int = 0
    locked: bool = False

    def reset(self) -> None:
        self.current_power = None
        self.max_power = None
        self.power_time_integral = 0.0
        self.valid_time = 0.0
        self.last_power = None
        self.last_timestamp = None
        self.abnormal_count = 0
        self.locked = False

    def add_power(self, timestamp: float, power: int) -> bool:
        if self.locked:
            return False
        if power < 0 or power > 3000:
            self.abnormal_count += 1
            return False

        if self.last_timestamp is not None and self.last_power is not None:
            dt = timestamp - self.last_timestamp
            if dt > 0:
                valid_dt = min(dt, self.dropout_threshold)
                self.power_time_integral += self.last_power * valid_dt
                self.valid_time += valid_dt

        self.current_power = power
        self.last_power = power
        self.last_timestamp = timestamp
        if self.max_power is None or power > self.max_power:
            self.max_power = power
        return True

    @property
    def average_power(self) -> float:
        if self.valid_time <= 0:
            return 0.0
        return self.power_time_integral / self.valid_time

    def lock(self) -> None:
        self.locked = True

    def finalize_until(self, timestamp: float) -> None:
        if self.locked or self.last_timestamp is None or self.last_power is None:
            return
        dt = timestamp - self.last_timestamp
        if dt <= 0:
            return
        valid_dt = min(dt, self.dropout_threshold)
        self.power_time_integral += self.last_power * valid_dt
        self.valid_time += valid_dt
        self.last_timestamp = timestamp


@dataclass
class TimeWeightedMetric:
    current_value: float | None = None
    max_value: float | None = None
    value_time_integral: float = 0.0
    valid_time: float = 0.0
    last_value: float | None = None
    last_timestamp: float | None = None
    locked: bool = False

    def reset(self) -> None:
        self.current_value = None
        self.max_value = None
        self.value_time_integral = 0.0
        self.valid_time = 0.0
        self.last_value = None
        self.last_timestamp = None
        self.locked = False

    def add_value(self, timestamp: float, value: float) -> None:
        if self.locked:
            return
        if self.last_timestamp is not None and self.last_value is not None:
            dt = timestamp - self.last_timestamp
            if dt > 0:
                self.value_time_integral += self.last_value * dt
                self.valid_time += dt
        self.current_value = value
        self.last_value = value
        self.last_timestamp = timestamp
        if self.max_value is None or value > self.max_value:
            self.max_value = value

    def finalize_until(self, timestamp: float) -> None:
        if self.locked or self.last_timestamp is None or self.last_value is None:
            return
        dt = timestamp - self.last_timestamp
        if dt <= 0:
            return
        self.value_time_integral += self.last_value * dt
        self.valid_time += dt
        self.last_timestamp = timestamp

    @property
    def average_value(self) -> float:
        if self.valid_time <= 0:
            return 0.0
        return self.value_time_integral / self.valid_time

    def lock(self) -> None:
        self.locked = True
