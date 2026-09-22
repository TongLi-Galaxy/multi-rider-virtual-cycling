"""Tacx-compatible FE-C over BLE, also advertised by ThinkRider X202.

Wire format reference (BLE uses an additive checksum, not ANT's XOR):
https://github.com/zacharyedwardbull/pycycling/blob/7ec67ef25474982f695b69cf1f652f6a172add4d/pycycling/tacx_trainer_control.py
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Any

FEC_SERVICE_UUID = "6e40fec1-b5a3-f393-e0a9-e50e24dcca9e"
FEC_NOTIFY_UUID = "6e40fec2-b5a3-f393-e0a9-e50e24dcca9e"
FEC_WRITE_UUID = "6e40fec3-b5a3-f393-e0a9-e50e24dcca9e"
FEC_RESPONSE_TIMEOUT = 2.0


def fec_frame(page: bytes) -> bytes:
    if len(page) != 8:
        raise ValueError("FE-C pages must contain 8 bytes")
    frame = bytes([0xA4, 9, 0x4F, 5]) + page
    return frame + bytes([sum(frame[1:]) & 0xFF])


def track_page(grade: float) -> bytes:
    if not math.isfinite(grade):
        raise ValueError("Grade must be finite")
    # FE-C grade is unsigned, offset by +200%, in units of 0.01%.
    encoded = round((min(25.0, max(-20.0, grade)) + 200) * 100)
    # FE-C Crr resolution is 0.00005 (different from FTMS).
    return b"\x33\xff\xff\xff\xff" + encoded.to_bytes(2, "little") + b"\x50"


def user_page(rider_kg: float, bike_kg: float) -> bytes:
    if not all(math.isfinite(value) for value in (rider_kg, bike_kg)):
        raise ValueError("Weights must be finite")
    rider = round(min(200.0, max(30.0, rider_kg)) * 100)
    bike = round(min(30.0, max(5.0, bike_kg)) / 0.05)
    # FE-C defaults for a road bike: 0.70 m wheel diameter, 2.49 gear ratio.
    # The application has no wheel/gear inputs; never send zero bike/rider mass.
    return b"\x37" + rider.to_bytes(2, "little") + bytes([
        0xFF, (bike & 0x0F) << 4, bike >> 4, 70, 83,
    ])


class FecControl:
    def __init__(self, client: Any, write_char: Any, notify_char: Any,
                 log: Callable[[str], None]) -> None:
        self.client = client
        self.write_char = write_char
        self.notify_char = notify_char
        self.log = log
        self.subscribed = False
        self._configuration: bytes | None = None
        self._pending: asyncio.Future[int] | None = None
        self._buffer = bytearray()
        self._last_page: bytes | None = None
        self._last_ack_at = 0.0
        self._last_sequence: int | None = None
        self._expected_data: bytes | None = None

    async def set_grade(self, grade: float, rider_kg: float = 70.0,
                        bike_kg: float = 10.0) -> None:
        page = track_page(grade)
        config = user_page(rider_kg, bike_kg)
        if (config == self._configuration and page == self._last_page
                and time.monotonic() - self._last_ack_at < 5):
            return
        try:
            self._last_page = None
            if not self.subscribed:
                await self.client.start_notify(self.notify_char, self._notification)
                self.subscribed = True
            if config != self._configuration:
                await self._write(config)
                # Non-zero aerodynamic load: Cw=0.51 kg/m, no wind, no drafting.
                await self._write(b"\x32\xff\xff\xff\xff\x33\x7f\x64")
                self._configuration = config
            self._pending = asyncio.get_running_loop().create_future()
            self._expected_data = page[4:]
            await self._write(page)
            # Page 70 asks for command status page 71. GATT write completion
            # alone is not an acknowledgement that the trainer applied grade.
            await self._write(b"\x46\xff\xff\xff\xff\x80\x47\x01")
            try:
                result = await asyncio.wait_for(self._pending, FEC_RESPONSE_TIMEOUT)
            except asyncio.TimeoutError:
                self.log(f"FE-C 坡度 {grade:.2f}% 已写入，但未收到设备确认，将重试")
                return
            if result == 0:
                self._last_page = page
                self._last_ack_at = time.monotonic()
                self.log(f"FE-C 坡度 {grade:.2f}% 设备已确认")
            else:
                reason = {1: "失败", 2: "不支持", 3: "拒绝", 4: "处理中", 255: "未初始化"}
                self.log(f"FE-C 坡度 {grade:.2f}% 未应用: {reason.get(result, str(result))}")
        except Exception as exc:
            self.log(f"FE-C 坡度推送失败: {exc}")
        finally:
            if self._pending is not None and not self._pending.done():
                self._pending.cancel()
            self._pending = None
            self._expected_data = None

    async def _write(self, page: bytes) -> None:
        payload = fec_frame(page)
        self.log(f"FE-C 控制发送: {payload.hex(' ')}")
        await self.client.write_gatt_char(
            self.write_char, payload,
            response="write" in self.write_char.properties,
        )

    def _notification(self, _sender: Any, data: bytearray) -> None:
        self._buffer.extend(data)
        while self._buffer:
            if self._buffer[0] != 0xA4:
                del self._buffer[0]
                continue
            if len(self._buffer) < 2:
                return
            if self._buffer[1] != 9:
                del self._buffer[0]
                continue
            if len(self._buffer) < 13:
                return
            frame = bytes(self._buffer[:13])
            del self._buffer[:13]
            xor = 0
            for value in frame[:-1]:
                xor ^= value
            if frame[-1] not in (sum(frame[1:-1]) & 0xFF, xor):
                self.log("FE-C 响应校验失败，已忽略")
                continue
            if frame[2] not in (0x4E, 0x4F) or frame[4] != 0x47:
                continue
            self.log(f"FE-C 控制响应: {frame.hex(' ')}")
            command, sequence, status = frame[5:8]
            if command != 0x33 or sequence == self._last_sequence:
                continue
            # Pending status may precede a final response with the same sequence.
            if status == 4:
                continue
            if status == 0 and frame[8:12] != self._expected_data:
                # A delayed status for a previous slope is not confirmation of
                # the current command (page 71 echoes the last four data bytes).
                continue
            self._last_sequence = sequence
            if self._pending is not None and not self._pending.done():
                self._pending.set_result(status)

    async def close(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        if self.subscribed:
            try:
                await self.client.stop_notify(self.notify_char)
            except Exception:
                pass
        self.subscribed = False
