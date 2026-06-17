from __future__ import annotations

import unittest

from app.core.exam_controller import EXAM_MODE_ROUTE, EXAM_MODE_TIME, MAX_RIDERS, ExamController
from app.core.rider_state import DeviceBinding, STATUS_CONNECTED, STATUS_DROPPED
from app.core.route import RouteProfile, RouteSegment
from app.core.simulation import BASE_RIDER_CDA, draft_aero_multiplier, estimate_rider_cda, leader_wake_factor


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

    def test_controller_supports_eight_simultaneous_riders(self) -> None:
        controller = ExamController(duration_seconds=60)
        self.assertEqual(len(controller.riders), MAX_RIDERS)
        for slot in range(1, MAX_RIDERS + 1):
            rider = controller.rider(slot)
            rider.apply_binding(DeviceBinding(slot=slot, device_name=f"Trainer {slot}", device_address=f"TEST-{slot}"))
            rider.connection_status = STATUS_CONNECTED

        ok, message = controller.prepare()
        self.assertTrue(ok, message)
        ok, message = controller.start()
        self.assertTrue(ok, message)
        self.assertEqual(controller.active_slots, set(range(1, MAX_RIDERS + 1)))

    def test_rider_count_limits_selected_slots(self) -> None:
        controller = ExamController(duration_seconds=60)
        controller.set_rider_count(4)

        self.assertEqual([rider.slot for rider in controller.selected_riders()], [1, 2, 3, 4])
        self.assertEqual(len(controller.summary_rows()), 4)

    def test_start_allows_unconnected_selected_riders(self) -> None:
        controller = ExamController(duration_seconds=60)
        controller.set_rider_count(4)
        for slot in (1, 2, 3, 4):
            rider = controller.rider(slot)
            rider.apply_binding(DeviceBinding(slot=slot, device_name=f"Trainer {slot}", device_address=f"TEST-{slot}"))
        for slot in (1, 3):
            controller.rider(slot).connection_status = STATUS_CONNECTED

        ok, message = controller.prepare()
        self.assertTrue(ok, message)
        ok, message = controller.start()
        self.assertTrue(ok, message)
        self.assertEqual(controller.active_slots, {1, 3})
        self.assertTrue(controller.rider(1).exam_running)
        self.assertFalse(controller.rider(2).exam_running)

    def test_draft_multiplier_depends_on_gap_and_speed(self) -> None:
        self.assertEqual(draft_aero_multiplier(3.0, 10.0 / 3.6), 1.0)
        self.assertEqual(draft_aero_multiplier(20.0, 12.0), 1.0)
        self.assertLess(draft_aero_multiplier(2.0, 12.0), 1.0)
        self.assertLess(draft_aero_multiplier(2.0, 12.0), draft_aero_multiplier(8.0, 12.0))
        self.assertLess(
            draft_aero_multiplier(2.0, 12.0, riders_ahead=3),
            draft_aero_multiplier(2.0, 12.0, riders_ahead=1),
        )

    def test_rider_cda_uses_weight_as_conservative_proxy(self) -> None:
        light = estimate_rider_cda(55.0)
        reference = estimate_rider_cda(70.0)
        heavy = estimate_rider_cda(95.0)
        very_heavy = estimate_rider_cda(180.0)

        self.assertAlmostEqual(reference, BASE_RIDER_CDA)
        self.assertLess(light, reference)
        self.assertGreater(heavy, reference)
        self.assertGreaterEqual(light, BASE_RIDER_CDA * 0.90)
        self.assertLessEqual(very_heavy, BASE_RIDER_CDA * 1.10)

    def test_leader_cda_changes_draft_wake_strength_slightly(self) -> None:
        light_leader = leader_wake_factor(estimate_rider_cda(50.0))
        heavy_leader = leader_wake_factor(estimate_rider_cda(100.0))

        self.assertLess(light_leader, 1.0)
        self.assertGreater(heavy_leader, 1.0)
        self.assertLess(
            draft_aero_multiplier(2.0, 12.0, leader_cda_factor=heavy_leader),
            draft_aero_multiplier(2.0, 12.0, leader_cda_factor=light_leader),
        )

    def test_route_drafting_tracks_nearest_leader(self) -> None:
        controller = ExamController(duration_seconds=60)
        controller.set_exam_mode(EXAM_MODE_ROUTE)
        controller.set_drafting_enabled(True)
        controller.set_route(RouteProfile([RouteSegment(1000.0, 0.0)]))
        for slot in (1, 2, 3):
            rider = controller.rider(slot)
            rider.apply_binding(DeviceBinding(slot=slot, device_name=f"Trainer {slot}", device_address=f"TEST-{slot}"))
            rider.connection_status = STATUS_CONNECTED

        ok, message = controller.prepare()
        self.assertTrue(ok, message)
        ok, message = controller.start()
        self.assertTrue(ok, message)
        assert controller.start_time is not None

        leader = controller.rider(1)
        follower = controller.rider(2)
        second_leader = controller.rider(3)
        leader.simulated_distance_m = 20.0
        second_leader.simulated_distance_m = 24.0
        follower.simulated_distance_m = 15.0
        follower.simulated_speed_mps = 12.0

        controller.tick(controller.start_time + 1.0)

        self.assertEqual(follower.draft_leader_slot, 1)
        self.assertEqual(follower.draft_riders_ahead, 2)
        self.assertLess(follower.draft_aero_multiplier, 1.0)
        self.assertGreater(follower.draft_savings_watts, 0.0)
        self.assertTrue(controller.summary_rows()[0]["drafting_enabled"])

    def test_drafting_uses_same_tick_snapshot_for_all_riders(self) -> None:
        controller = ExamController(duration_seconds=60)
        controller.set_exam_mode(EXAM_MODE_ROUTE)
        controller.set_drafting_enabled(True)
        controller.set_route(RouteProfile([RouteSegment(1000.0, 0.0)]))
        for slot in (1, 2):
            rider = controller.rider(slot)
            rider.apply_binding(DeviceBinding(slot=slot, device_name=f"Trainer {slot}", device_address=f"TEST-{slot}"))
            rider.connection_status = STATUS_CONNECTED

        ok, message = controller.prepare()
        self.assertTrue(ok, message)
        ok, message = controller.start()
        self.assertTrue(ok, message)
        assert controller.start_time is not None

        leader = controller.rider(1)
        follower = controller.rider(2)
        leader.simulated_distance_m = 26.9
        leader.simulated_speed_mps = 5.0
        follower.simulated_distance_m = 15.0
        follower.simulated_speed_mps = 12.0

        controller.tick(controller.start_time + 1.0)

        self.assertEqual(follower.draft_leader_slot, 1)
        self.assertAlmostEqual(follower.draft_gap_m or 0.0, 11.9)
        self.assertLess(follower.draft_aero_multiplier, 1.0)


if __name__ == "__main__":
    unittest.main()
