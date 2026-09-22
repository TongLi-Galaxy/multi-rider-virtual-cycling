from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ble.device_client import TrainerDeviceClient
from app.ble.scanner import FTMS_CONTROL_POINT_UUID, FTMS_INDOOR_BIKE_DATA_UUID


class FakeTrainer:
    def __init__(self, *args, **kwargs):
        self.is_connected = True
        self.callbacks = {}
        self.writes = []
        self.results = {}
        self.control = SimpleNamespace(
            uuid=FTMS_CONTROL_POINT_UUID, properties=["write", "indicate"]
        )
        self.services = [SimpleNamespace(characteristics=[
            self.control, SimpleNamespace(uuid=FTMS_INDOOR_BIKE_DATA_UUID)
        ])]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.is_connected = False

    async def start_notify(self, characteristic, callback):
        uuid = getattr(characteristic, "uuid", characteristic)
        self.callbacks[uuid] = callback

    async def stop_notify(self, characteristic):
        pass

    async def write_gatt_char(self, characteristic, payload, response):
        self.writes.append(bytes(payload))
        result = self.results.get(payload[0], 1)
        if result is not None:
            self.callbacks[FTMS_CONTROL_POINT_UUID](
                characteristic, bytearray([0x80, payload[0], result])
            )


class TrainerControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.logs = []
        self.client = TrainerDeviceClient(
            1, "TEST", "Test", lambda *args: None, lambda *args: None,
            self.logs.append,
        )
        self.trainer = FakeTrainer()
        self.client._ble_client = self.trainer
        self.client._control_point_char = self.trainer.control

    async def test_power_only_connection_does_not_take_control(self):
        self.client._stop_requested = True
        with patch("app.ble.device_client.BleakClient", return_value=self.trainer):
            await self.client._connect_and_listen()
        self.assertEqual(self.trainer.writes, [])
        self.assertIn(FTMS_INDOOR_BIKE_DATA_UUID, self.trainer.callbacks)

    async def test_simulation_payload_and_start_are_acknowledged(self):
        await self.client.set_simulation_grade(5.0)
        self.assertEqual(self.trainer.writes, [
            b"\x00", b"\x11\x00\x00\xf4\x01\x28\x33", b"\x07"
        ])
        await self.client.set_simulation_grade(5.0)
        self.assertEqual(len(self.trainer.writes), 3)
        await self.client.set_simulation_grade(-2.5)
        self.assertEqual(self.trainer.writes[-1], b"\x11\x00\x00\x06\xff\x28\x33")

    async def test_flat_road_keeps_rolling_and_wind_resistance(self):
        await self.client.set_simulation_grade(0)
        self.assertEqual(self.trainer.writes[1], b"\x11\x00\x00\x00\x00\x28\x33")

    async def test_control_rejection_prevents_simulation(self):
        self.trainer.results[0x00] = 5
        await self.client.set_simulation_grade(5)
        self.assertEqual(self.trainer.writes, [b"\x00"])
        self.assertIsNone(self.client._last_grade_sent)

    async def test_request_timeout_does_not_send_simulation(self):
        self.trainer.results[0x00] = None
        with patch("app.ble.device_client.FTMS_CONTROL_RESPONSE_TIMEOUT", 0.001):
            await self.client.set_simulation_grade(5)
        self.assertFalse(self.client._has_requested_control)
        self.assertEqual(self.trainer.writes, [b"\x00"])
        self.assertEqual(self.client._pending_control_response, {})

    async def test_cancelled_push_cleans_pending_response(self):
        self.trainer.results[0x00] = None
        task = asyncio.create_task(self.client.set_simulation_grade(5))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.client._pending_control_response, {})
        self.assertIsNone(self.client._last_grade_sent)

    async def test_concurrent_pushes_are_serialized(self):
        await asyncio.gather(*[
            self.client.set_simulation_grade(grade) for grade in (5, 5, 6)
        ])
        self.assertEqual([p[0] for p in self.trainer.writes], [0x00, 0x11, 0x07, 0x11])
        self.assertEqual(self.client._last_grade_sent, 6)

    async def test_timeout_is_not_success_and_same_grade_is_retried(self):
        self.trainer.results[0x11] = None
        with patch("app.ble.device_client.FTMS_CONTROL_RESPONSE_TIMEOUT", 0.001):
            await self.client.set_simulation_grade(5)
        self.assertIsNone(self.client._last_grade_sent)
        self.assertNotIn(b"\x07", self.trainer.writes)
        self.trainer.results[0x11] = 1
        await self.client.set_simulation_grade(5)
        self.assertEqual(self.client._last_grade_sent, 5)
        self.assertEqual(sum(p[0] == 0x11 for p in self.trainer.writes), 2)

    async def test_start_rejection_is_not_cached_as_applied(self):
        self.trainer.results[0x07] = 4
        await self.client.set_simulation_grade(5)
        self.assertIsNone(self.client._last_grade_sent)
        self.trainer.results[0x07] = 1
        await self.client.set_simulation_grade(5)
        self.assertTrue(self.client._simulation_started)
        self.assertEqual(self.client._last_grade_sent, 5)

    async def test_control_loss_reacquires_before_retry(self):
        await self.client.set_simulation_grade(5)
        self.trainer.results[0x11] = 5
        await self.client.set_simulation_grade(6)
        self.assertFalse(self.client._has_requested_control)
        self.trainer.results[0x11] = 1
        await self.client.set_simulation_grade(6)
        self.assertEqual([p[0] for p in self.trainer.writes[-3:]], [0x00, 0x11, 0x07])

    async def test_reconnect_clears_grade_cache(self):
        await self.client.set_simulation_grade(5)
        self.client._stop_requested = True
        replacement = FakeTrainer()
        with patch("app.ble.device_client.BleakClient", return_value=replacement):
            await self.client._connect_and_listen()
        self.assertIsNone(self.client._last_grade_sent)
        self.assertFalse(self.client._simulation_started)
        replacement.is_connected = True
        self.client._ble_client = replacement
        self.client._control_point_char = replacement.control
        await self.client.set_simulation_grade(5)
        self.assertEqual([p[0] for p in replacement.writes], [0x00, 0x11, 0x07])

    async def test_missing_indications_prevents_unconfirmed_control(self):
        self.trainer.control.properties = ["write"]
        await self.client.set_simulation_grade(5)
        self.assertEqual(self.trainer.writes, [])

    async def test_invalid_grade_is_not_sent(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            await self.client.set_simulation_grade(value)
        self.assertEqual(self.trainer.writes, [])


if __name__ == "__main__":
    unittest.main()
