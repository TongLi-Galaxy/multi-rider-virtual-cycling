from __future__ import annotations

import unittest

from app.core.exam_controller import EXAM_MODE_ROUTE, EXAM_MODE_TIME, ExamController
from app.core.rider_state import DeviceBinding, STATUS_CONNECTED, STATUS_DROPPED
from app.core.route import RouteProfile, RouteSegment


class ExamBehaviourTests(unittest.TestCase):
    def _controller_with_one_rider(self, mode: str = EXAM_MODE_TIME) -> ExamController:
        controller = ExamController(duration_seconds=60)
        controller.set_exam_mode(mode)
        controller.set_route(RouteProfile([RouteSegment(1000.0, 0.0)]))
        rider = controller.rider(1)
        rider.apply_binding(DeviceBinding(slot=1, device_name="Test Trainer", device_address="TEST-01"))
        rider.connection_status = STATUS_CONNECTED
        ok, message = controller.prepare()
        self.assertTrue(ok, message)
        ok, message = controller.start()
        self.assertTrue(ok, message)
        return controller

    def test_stale_power_is_not_kept_for_simulation(self) -> None:
        controller = self._controller_with_one_rider()
        rider = controller.rider(1)
        assert controller.start_time is not None

        controller.update_power(1, 220, controller.start_time + 0.1)
        controller.tick(controller.start_time + 1.0)
        self.assertEqual(rider.metrics.current_power, 220)

        controller.tick(controller.start_time + 4.0)
        self.assertIsNone(rider.metrics.current_power)
        self.assertEqual(rider.connection_status, STATUS_DROPPED)

    def test_route_mode_summary_uses_finish_time(self) -> None:
        controller = self._controller_with_one_rider(EXAM_MODE_ROUTE)
        controller.set_route(RouteProfile([RouteSegment(1.0, 0.0)]))
        rider = controller.rider(1)
        assert controller.start_time is not None

        controller.update_power(1, 500, controller.start_time + 0.1)
        finished = controller.tick(controller.start_time + 2.0)

        self.assertTrue(finished)
        self.assertEqual(rider.final_status, "completed")
        finish_time = controller.summary_rows()[0]["finish_time_seconds"]
        self.assertGreater(finish_time, 0.0)
        self.assertLess(finish_time, 2.0)

    def test_heart_rate_is_blank_without_real_sensor_data(self) -> None:
        controller = self._controller_with_one_rider()
        rider = controller.rider(1)
        assert controller.start_time is not None

        controller.update_power(1, 200, controller.start_time + 0.1)
        controller.tick(controller.start_time + 1.0)

        self.assertIsNone(rider.heart_rate_metrics.current_value)
        summary = controller.summary_rows()[0]
        self.assertEqual(summary["average_heart_rate"], "")
        self.assertEqual(summary["max_heart_rate"], "")


if __name__ == "__main__":
    unittest.main()
