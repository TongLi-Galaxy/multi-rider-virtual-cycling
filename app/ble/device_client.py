from __future__ import annotations

import asyncio
import math
import random
import time
from collections.abc import Callable
from typing import Any

from bleak import BleakClient

from app.ble.parsers import (
    parse_cycling_power_measurement,
    parse_ftms_indoor_bike_data,
)
from app.ble.scanner import (
    CYCLING_POWER_MEASUREMENT_UUID,
    FTMS_CONTROL_POINT_UUID,
    FTMS_INDOOR_BIKE_DATA_UUID,
    normalize_uuid,
)

PowerCallback = Callable[[int, int, float], None]
StatusCallback = Callable[[int, str, str], None]
LogCallback = Callable[[str], None]

STATUS_DISCONNECTED = "未连接"
STATUS_CONNECTING = "连接中"
STATUS_CONNECTED = "已连接"
STATUS_DATA_OK = "数据正常"
STATUS_DROPPED = "掉线"
STATUS_UNSUPPORTED = "不支持"

MAX_REASONABLE_POWER = 3000


class TrainerDeviceClient:
    def __init__(
        self,
        slot: int,
        address: str,
        name: str,
        power_callback: PowerCallback,
        status_callback: StatusCallback,
        log_callback: LogCallback | None = None,
        no_data_timeout: float = 3.0,
    ) -> None:
        self.slot = slot
        self.address = address
        self.name = name or address
        self.power_callback = power_callback
        self.status_callback = status_callback
        self.log_callback = log_callback or (lambda _message: None)
        self.no_data_timeout = no_data_timeout
        self._stop_requested = False
        self._last_data_monotonic: float | None = None
        self._connected = False
        self._status = STATUS_DISCONNECTED
        self._ble_client: BleakClient | None = None
        self._control_point_uuid: str | None = None
        self._has_requested_control = False
        self._last_grade_sent: float | None = None

    async def run(self) -> None:
        while not self._stop_requested:
            self._emit_status(STATUS_CONNECTING, f"{self.name} 正在连接")
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._emit_status(STATUS_DROPPED, f"{self.name} 连接失败: {exc}")
                self.log_callback(f"[{self.slot}号] 连接失败: {exc}")

            if self._stop_requested or self._status == STATUS_UNSUPPORTED:
                break

            await asyncio.sleep(2.0)

        if self._status != STATUS_UNSUPPORTED:
            self._emit_status(STATUS_DISCONNECTED, f"{self.name} 已停止")

    async def stop(self) -> None:
        self._stop_requested = True

    async def _connect_and_listen(self) -> None:
        self._connected = False
        self._ble_client = None
        self._control_point_uuid = None

        def handle_disconnect(_client: BleakClient) -> None:
            self._connected = False
            if not self._stop_requested:
                self._emit_status(STATUS_DROPPED, f"{self.name} 异常断开")

        async with BleakClient(
            self.address,
            disconnected_callback=handle_disconnect,
            timeout=10.0,
        ) as client:
            self._ble_client = client
            self._connected = bool(client.is_connected)
            if not self._connected:
                raise RuntimeError("BLE client did not connect")

            self._emit_status(STATUS_CONNECTED, f"{self.name} 已连接")
            power_char = await self._select_power_characteristic(client)
            self._control_point_uuid = await self._select_control_point(client)
            if not power_char:
                self._emit_status(STATUS_UNSUPPORTED, f"{self.name} 不支持功率读取")
                return

            await self._prepare_control_point(client)
            source = (
                "FTMS Indoor Bike Data"
                if power_char == FTMS_INDOOR_BIKE_DATA_UUID
                else "Cycling Power Measurement"
            )
            self.log_callback(f"[{self.slot}号] 订阅 {source}: {self.name}")

            self._last_data_monotonic = time.monotonic()

            def notification_handler(_sender: Any, payload: bytearray) -> None:
                self._handle_notification(power_char, payload)

            try:
                await client.start_notify(power_char, notification_handler)
            except Exception as exc:
                raise RuntimeError(f"通知订阅失败: {exc}") from exc

            try:
                while self._connected and not self._stop_requested:
                    await asyncio.sleep(0.25)
                    if self._last_data_monotonic is None:
                        continue
                    silent_for = time.monotonic() - self._last_data_monotonic
                    if silent_for > self.no_data_timeout:
                        self._emit_status(STATUS_DROPPED, f"{self.name} 超过 3 秒无数据")
            finally:
                try:
                    await client.stop_notify(power_char)
                except Exception:
                    pass
                if self._control_point_uuid:
                    try:
                        await client.stop_notify(self._control_point_uuid)
                    except Exception:
                        pass
                self._ble_client = None
                self._control_point_uuid = None

    async def _select_power_characteristic(self, client: BleakClient) -> str | None:
        service_collection = getattr(client, "services", None)
        if service_collection is None and hasattr(client, "get_services"):
            service_collection = await client.get_services()

        char_uuids: set[str] = set()

        if service_collection is not None:
            for service in service_collection:
                for characteristic in service.characteristics:
                    char_uuids.add(normalize_uuid(str(characteristic.uuid)))

        if FTMS_INDOOR_BIKE_DATA_UUID in char_uuids:
            return FTMS_INDOOR_BIKE_DATA_UUID
        if CYCLING_POWER_MEASUREMENT_UUID in char_uuids:
            return CYCLING_POWER_MEASUREMENT_UUID
        return None

    async def _select_control_point(self, client: BleakClient) -> str | None:
        service_collection = getattr(client, "services", None)
        if service_collection is None and hasattr(client, "get_services"):
            service_collection = await client.get_services()

        if service_collection is None:
            return None

        for service in service_collection:
            for characteristic in service.characteristics:
                if normalize_uuid(str(characteristic.uuid)) == FTMS_CONTROL_POINT_UUID:
                    return FTMS_CONTROL_POINT_UUID
        return None

    async def _prepare_control_point(self, client: BleakClient) -> None:
        if not self._control_point_uuid:
            self.log_callback(f"[{self.slot}号] 未发现 FTMS 控制点，无法推送坡度")
            return

        def control_response_handler(_sender: Any, payload: bytearray) -> None:
            self.log_callback(f"[{self.slot}号] FTMS 控制响应: {payload.hex(' ')}")

        try:
            await client.start_notify(self._control_point_uuid, control_response_handler)
        except Exception as exc:
            self.log_callback(f"[{self.slot}号] FTMS 控制响应订阅失败: {exc}")

        try:
            # Bluetooth SIG FTMS Control Point: 0x00 Request Control.
            await client.write_gatt_char(self._control_point_uuid, bytes([0x00]), response=True)
            self._has_requested_control = True
            self.log_callback(f"[{self.slot}号] 已请求 FTMS 控制权")
        except Exception as exc:
            self.log_callback(f"[{self.slot}号] 请求 FTMS 控制权失败: {exc}")

    async def set_simulation_grade(self, grade_percent: float) -> None:
        if not self._ble_client or not self._ble_client.is_connected:
            return
        if not self._control_point_uuid:
            return

        grade = min(25.0, max(-20.0, float(grade_percent)))
        if self._last_grade_sent is not None and abs(grade - self._last_grade_sent) < 0.1:
            return

        # Bluetooth SIG FTMS Control Point: 0x11 Set Indoor Bike Simulation
        # Parameters. Payload is wind speed (sint16, 0.001 m/s), grade
        # (sint16, 0.01%), rolling resistance coefficient (uint8, 0.0001),
        # and wind resistance coefficient (uint8, 0.01 kg/m).
        payload = bytearray([0x11])
        payload += int(0).to_bytes(2, byteorder="little", signed=True)
        payload += int(round(grade * 100)).to_bytes(2, byteorder="little", signed=True)
        payload += int(40).to_bytes(1, byteorder="little", signed=False)
        payload += int(51).to_bytes(1, byteorder="little", signed=False)

        try:
            if not self._has_requested_control:
                await self._prepare_control_point(self._ble_client)
            await self._ble_client.write_gatt_char(
                self._control_point_uuid,
                bytes(payload),
                response=True,
            )
            self._last_grade_sent = grade
        except Exception as exc:
            self.log_callback(f"[{self.slot}号] 坡度推送失败: {exc}")

    def _handle_notification(self, characteristic_uuid: str, payload: bytearray) -> None:
        try:
            if characteristic_uuid == FTMS_INDOOR_BIKE_DATA_UUID:
                parsed = parse_ftms_indoor_bike_data(payload)
            else:
                parsed = parse_cycling_power_measurement(payload)

            if not parsed or not parsed.get("power_present"):
                self.log_callback(f"[{self.slot}号] 收到数据但没有功率字段")
                return

            power = parsed.get("instantaneous_power")
            if not isinstance(power, int):
                return

            now = time.time()
            self._last_data_monotonic = time.monotonic()

            if power < 0 or power > MAX_REASONABLE_POWER:
                self.log_callback(f"[{self.slot}号] 异常功率 {power}W，已忽略")
                return

            self._emit_status(STATUS_DATA_OK, f"{self.name} 数据正常")
            self.power_callback(self.slot, power, now)
        except Exception as exc:
            self.log_callback(f"[{self.slot}号] 功率数据解析失败: {exc}")

    def _emit_status(self, status: str, message: str = "") -> None:
        if status == self._status:
            return
        self._status = status
        self.status_callback(self.slot, status, message)


class MockTrainerDeviceClient:
    def __init__(
        self,
        slot: int,
        power_callback: PowerCallback,
        status_callback: StatusCallback,
        log_callback: LogCallback | None = None,
    ) -> None:
        self.slot = slot
        self.name = f"Mock Trainer {slot}"
        self.power_callback = power_callback
        self.status_callback = status_callback
        self.log_callback = log_callback or (lambda _message: None)
        self._stop_requested = False
        self._t = 0.0
        self._last_grade = 0.0

    async def run(self) -> None:
        self.status_callback(self.slot, STATUS_CONNECTING, f"{self.name} 正在连接")
        await asyncio.sleep(0.2)
        self.status_callback(self.slot, STATUS_CONNECTED, f"{self.name} 已连接")
        await asyncio.sleep(0.2)
        self.status_callback(self.slot, STATUS_DATA_OK, f"{self.name} 数据正常")
        self.log_callback(f"[{self.slot}号] mock 功率源已启动")

        base = 135 + self.slot * 28
        while not self._stop_requested:
            self._t += 0.85
            wave = math.sin(self._t / 8.0 + self.slot) * 38
            surge = math.sin(self._t / 2.7) * 11
            noise = random.randint(-10, 10)
            grade_load = max(0.0, self._last_grade) * 5.0
            power = max(0, int(base + wave + surge + grade_load + noise))
            self.power_callback(self.slot, power, time.time())
            await asyncio.sleep(random.uniform(0.7, 1.15))

        self.status_callback(self.slot, STATUS_DISCONNECTED, f"{self.name} 已停止")

    async def stop(self) -> None:
        self._stop_requested = True

    async def set_simulation_grade(self, grade_percent: float) -> None:
        self._last_grade = float(grade_percent)
