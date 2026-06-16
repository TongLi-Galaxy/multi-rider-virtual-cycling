from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from app.core.route import RouteProfile


MIN_DISPLAY_ELEVATION_SPAN_M = 12.0
BASE_HEIGHT_RATIO = 0.35
TOP_HEADROOM_RATIO = 0.12


class RouteProfileWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._route = RouteProfile()
        self._rider_distances: dict[int, float] = {}
        self.setMinimumHeight(190)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    def set_route(self, route: RouteProfile) -> None:
        self._route = route
        self.update()

    def set_rider_distances(self, distances: dict[int, float]) -> None:
        self._rider_distances = distances
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(12, 10, -12, -12)
        painter.fillRect(event.rect(), QtGui.QColor("#ffffff"))
        if rect.width() <= 10 or rect.height() <= 10:
            return

        points = self._route.elevation_points()
        if len(points) < 2:
            return

        total_distance = max(1.0, points[-1][0])
        elevations = [point[1] for point in points]
        min_elevation, max_elevation = _display_elevation_bounds(elevations)
        span = max(1.0, max_elevation - min_elevation)

        chart = rect.adjusted(0, 2, 0, -8)
        baseline_y = chart.bottom()

        def map_point(distance_m: float, elevation_m: float) -> QtCore.QPointF:
            x = chart.left() + (distance_m / total_distance) * chart.width()
            y = chart.bottom() - ((elevation_m - min_elevation) / span) * chart.height()
            return QtCore.QPointF(x, y)

        mapped = [map_point(distance, elevation) for distance, elevation in points]

        grid_pen = QtGui.QPen(QtGui.QColor("#dce4ea"), 1)
        painter.setPen(grid_pen)
        for index in range(1, 5):
            y = chart.top() + chart.height() * index / 5
            painter.drawLine(chart.left(), int(y), chart.right(), int(y))

        area = QtGui.QPainterPath()
        area.moveTo(mapped[0].x(), baseline_y)
        for point in mapped:
            area.lineTo(point)
        area.lineTo(mapped[-1].x(), baseline_y)
        area.closeSubpath()

        painter.fillPath(area, QtGui.QColor("#92cf2b"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#6aaa1e"), 3))
        for index in range(1, len(mapped)):
            painter.drawLine(mapped[index - 1], mapped[index])

        painter.setPen(QtGui.QPen(QtGui.QColor("#24313d"), 2))
        painter.drawLine(chart.left(), baseline_y, chart.right(), baseline_y)

        segment_x = chart.left()
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
        for segment in self._route.segments[:-1]:
            segment_x += segment.distance_m / total_distance * chart.width()
            painter.drawLine(int(segment_x), chart.top(), int(segment_x), baseline_y)

        colors = [
            "#dc3b35",
            "#2f80ed",
            "#8e44ad",
            "#f39c12",
            "#009688",
            "#6d4c41",
            "#546e7a",
            "#c2185b",
        ]
        for slot, distance in sorted(self._rider_distances.items()):
            if total_distance <= 0:
                continue
            cursor = min(max(0.0, distance), total_distance)
            grade = self._route.grade_at(cursor, loop=False)
            elevation = self._elevation_at(cursor)
            center = map_point(cursor, elevation)
            color = QtGui.QColor(colors[(slot - 1) % len(colors)])
            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
            marker_radius = 9
            painter.drawEllipse(center, marker_radius, marker_radius)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
            font = painter.font()
            font.setBold(True)
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(
                QtCore.QRectF(
                    center.x() - marker_radius,
                    center.y() - marker_radius,
                    marker_radius * 2,
                    marker_radius * 2,
                ),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                str(slot),
            )
            painter.setPen(QtGui.QPen(color.darker(130), 1))
            painter.drawLine(
                QtCore.QPointF(center.x(), center.y() + marker_radius + 1),
                QtCore.QPointF(center.x(), baseline_y),
            )
            _ = grade

    def _elevation_at(self, distance_m: float) -> float:
        cursor = max(0.0, distance_m)
        covered = 0.0
        elevation = 0.0
        for segment in self._route.segments:
            if cursor <= covered + segment.distance_m:
                return elevation + (cursor - covered) * segment.grade_percent / 100.0
            covered += segment.distance_m
            elevation += segment.distance_m * segment.grade_percent / 100.0
        return elevation


def _display_elevation_bounds(elevations: list[float]) -> tuple[float, float]:
    min_elevation = min(elevations)
    max_elevation = max(elevations)
    raw_span = max_elevation - min_elevation
    route_height_ratio = max(0.05, 1.0 - BASE_HEIGHT_RATIO - TOP_HEADROOM_RATIO)

    display_span = max(
        MIN_DISPLAY_ELEVATION_SPAN_M,
        raw_span / route_height_ratio if raw_span > 0 else MIN_DISPLAY_ELEVATION_SPAN_M,
    )
    display_min = min_elevation - display_span * BASE_HEIGHT_RATIO
    return display_min, display_min + display_span
