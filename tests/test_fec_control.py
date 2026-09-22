from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ble.device_client import TrainerDeviceClient
from app.ble.fec import (
    FEC_NOTIFY_UUID, FEC_SERVICE_UUID, FEC_WRITE_UUID, FecControl,
    fec_frame, track_page, user_page,
)
from app.ble.scanner import CYCLING_POWER_MEASUREMENT_UUID, ScannedDevice


class FecTrainer:
    def __init__(self):
        self.is_connected = True
        self.write = SimpleNamespace(uuid=FEC_WRITE_UUID, properties=["write"])
        self.notify = SimpleNamespace(uuid=FEC_NOTIFY_UUID, properties=["notify"])
        self.services = [SimpleNamespace(characteristics=[self.write, self.notify,
            SimpleNamespace(uuid=CYCLING_POWER_MEASUREMENT_UUID)])]
        self.writes = []
        self.callbacks = {}
        self.status = 0
        self.sequence = 0
        self.track = bytes(8)
        self.fail_write = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.is_connected = False

    async def start_notify(self, char, callback):
        self.callbacks[getattr(char, "uuid", char)] = callback

    async def stop_notify(self, char):
        self.callbacks.pop(getattr(char, "uuid", char), None)

    async def write_gatt_char(self, char, data, response):
        if self.fail_write:
            raise OSError("simulated write failure")
        self.writes.append(bytes(data))
        if data[4] == 0x33:
            self.track = data[4:12]
            self.sequence = (self.sequence + 1) % 256
        if data[4] == 0x46 and self.status is not None:
            # Independent device response fixture: broadcast status page 71.
            reply = bytearray([0xA4, 9, 0x4E, 5, 0x47, 0x33,
                               self.sequence, self.status])
            reply.extend(self.track[4:])
            reply.append(sum(reply[1:]) & 255)
            self.callbacks[FEC_NOTIFY_UUID](self.notify, reply)


class FecEncodingTests(unittest.TestCase):
    def test_twenty_percent_uphill_wire_vector(self):
        self.assertEqual(fec_frame(track_page(20)),
                         bytes.fromhex("a4 09 4f 05 33 ff ff ff ff f0 55 50 21"))

    def test_flat_and_downhill_have_offset_and_nonzero_rolling_load(self):
        self.assertEqual(fec_frame(track_page(0)),
                         bytes.fromhex("a4 09 4f 05 33 ff ff ff ff 20 4e 50 4a"))
        self.assertEqual(track_page(-5)[5:7], bytes.fromhex("2c 4c"))

    def test_weight_configuration_matches_rider_and_bike(self):
        self.assertEqual(user_page(59, 8), bytes.fromhex("37 0c 17 ff 00 0a 46 53"))

    def test_invalid_values_and_clamping(self):
        with self.assertRaises(ValueError):
            track_page(float("nan"))
        self.assertEqual(track_page(100), track_page(25))
        self.assertEqual(track_page(-100), track_page(-20))

    def test_scanner_recognizes_actual_x2_advertisement(self):
        device = ScannedDevice("Think X202", "TEST", service_uuids=[
            "00001818-0000-1000-8000-00805f9b34fb", FEC_SERVICE_UUID])
        self.assertTrue(device.supports_fec)
        self.assertFalse(device.supports_ftms)
        self.assertIn("FE-C", device.support_label)


class FecControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.trainer = FecTrainer()
        self.logs = []
        self.control = FecControl(self.trainer, self.trainer.write,
                                  self.trainer.notify, self.logs.append)

    async def test_configure_then_send_uphill_and_confirm(self):
        await self.control.set_grade(20, 59, 8)
        self.assertEqual([p[4] for p in self.trainer.writes], [0x37, 0x32, 0x33, 0x46])
        self.assertEqual(self.control._last_page, track_page(20))
        await self.control.set_grade(20, 59, 8)
        self.assertEqual(len(self.trainer.writes), 4)
        await self.control.set_grade(0, 59, 8)
        self.assertEqual(self.control._last_page, track_page(0))

    async def test_missing_status_retries_instead_of_claiming_success(self):
        self.trainer.status = None
        with patch("app.ble.fec.FEC_RESPONSE_TIMEOUT", 0.001):
            await self.control.set_grade(20)
            await self.control.set_grade(20)
        self.assertIsNone(self.control._last_page)
        self.assertEqual(sum(p[4] == 0x33 for p in self.trainer.writes), 2)

    async def test_rejected_and_failed_writes_are_not_cached(self):
        self.trainer.status = 2
        await self.control.set_grade(20)
        self.assertIsNone(self.control._last_page)
        self.trainer.fail_write = True
        await self.control.set_grade(5)
        self.assertIsNone(self.control._last_page)
        self.assertIsNone(self.control._pending)

    async def test_old_grade_status_does_not_acknowledge_new_grade(self):
        self.control._pending = asyncio.get_running_loop().create_future()
        self.control._expected_data = track_page(20)[4:]
        old = fec_frame(bytes([0x47, 0x33, 1, 0]) + track_page(0)[4:])
        self.control._notification(None, bytearray(old))
        self.assertFalse(self.control._pending.done())
        self.control._pending.cancel()

    async def test_split_frames_and_corrupt_status(self):
        self.control._pending = asyncio.get_running_loop().create_future()
        self.control._expected_data = track_page(20)[4:]
        reply = fec_frame(bytes([0x47, 0x33, 1, 0]) + track_page(20)[4:])
        bad = bytearray(reply)
        bad[-1] ^= 1
        self.control._notification(None, bad)
        self.assertFalse(self.control._pending.done())
        self.control._notification(None, bytearray(reply[:6]))
        self.assertFalse(self.control._pending.done())
        self.control._notification(None, bytearray(reply[6:]))
        self.assertEqual(self.control._pending.result(), 0)

    async def test_cancel_cleans_response_waiter(self):
        self.trainer.status = None
        task = asyncio.create_task(self.control.set_grade(20))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.control._pending)

    async def test_x2_discovery_is_read_only_and_reconnect_creates_fresh_control(self):
        client = TrainerDeviceClient(1, "TEST", "Think X202",
                                     lambda *args: None, lambda *args: None, self.logs.append)
        client._stop_requested = True
        with patch("app.ble.device_client.BleakClient", return_value=self.trainer):
            await client._connect_and_listen()
        self.assertEqual(self.trainer.writes, [])
        self.assertTrue(any("FE-C over BLE" in message for message in self.logs))
        self.assertIsNone(client._fec)
        self.trainer.is_connected = True
        client._ble_client = self.trainer
        client._fec = await client._select_fec_control(self.trainer)
        await client.set_simulation_grade(20, 59, 8)
        self.assertEqual(client._fec._last_page, track_page(20))


if __name__ == "__main__":
    unittest.main()
