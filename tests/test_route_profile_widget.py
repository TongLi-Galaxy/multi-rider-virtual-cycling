from __future__ import annotations

import unittest

from app.gui.route_profile_widget import _display_elevation_bounds


class RouteProfileWidgetTests(unittest.TestCase):
    def test_flat_route_has_visual_base_height(self) -> None:
        display_min, display_max = _display_elevation_bounds([0.0, 0.0, 0.0])

        self.assertLess(display_min, 0.0)
        self.assertGreater(display_max, 0.0)


if __name__ == "__main__":
    unittest.main()
