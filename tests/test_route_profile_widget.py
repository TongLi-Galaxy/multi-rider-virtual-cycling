from __future__ import annotations

import unittest

from app.gui.route_profile_widget import (
    BASE_HEIGHT_RATIO,
    TOP_HEADROOM_RATIO,
    _display_elevation_bounds,
)


class RouteProfileWidgetTests(unittest.TestCase):
    def test_flat_route_has_visual_base_height(self) -> None:
        display_min, display_max = _display_elevation_bounds([0.0, 0.0, 0.0])

        self.assertLess(display_min, 0.0)
        self.assertGreater(display_max, 0.0)

    def test_high_route_has_top_headroom(self) -> None:
        display_min, display_max = _display_elevation_bounds([0.0, 120.0, 260.0])
        display_span = display_max - display_min

        self.assertGreater(display_max, 260.0)
        self.assertGreaterEqual((display_max - 260.0) / display_span, TOP_HEADROOM_RATIO - 1e-9)

    def test_bottom_base_height_is_preserved(self) -> None:
        min_elevation = -30.0
        display_min, display_max = _display_elevation_bounds([0.0, 80.0, min_elevation, 150.0])
        display_span = display_max - display_min

        self.assertLess(display_min, min_elevation)
        self.assertGreaterEqual((min_elevation - display_min) / display_span, BASE_HEIGHT_RATIO - 1e-9)


if __name__ == "__main__":
    unittest.main()
