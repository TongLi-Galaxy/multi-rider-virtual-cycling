from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class RouteSegment:
    distance_m: float
    grade_percent: float

    @classmethod
    def from_dict(cls, data: dict) -> "RouteSegment":
        return cls(
            distance_m=max(1.0, float(data.get("distance_m", 100.0))),
            grade_percent=min(25.0, max(-20.0, float(data.get("grade_percent", 0.0)))),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "distance_m": round(self.distance_m, 2),
            "grade_percent": round(self.grade_percent, 2),
        }


class RouteProfile:
    def __init__(self, segments: list[RouteSegment] | None = None) -> None:
        self.segments = segments or default_route_segments()

    @classmethod
    def from_dict(cls, data: dict) -> "RouteProfile":
        raw_segments = list(data.get("segments", []) or [])
        segments = [RouteSegment.from_dict(item) for item in raw_segments]
        return cls(segments or default_route_segments())

    def to_dict(self) -> dict[str, object]:
        return {"segments": [segment.to_dict() for segment in self.segments]}

    @property
    def total_distance_m(self) -> float:
        return sum(segment.distance_m for segment in self.segments)

    def grade_at(self, distance_m: float, loop: bool = True) -> float:
        if not self.segments:
            return 0.0

        total = self.total_distance_m
        if total <= 0:
            return 0.0

        if loop:
            cursor = max(0.0, distance_m) % total
        else:
            cursor = min(max(0.0, distance_m), total)
        covered = 0.0
        for segment in self.segments:
            covered += segment.distance_m
            if cursor <= covered:
                return segment.grade_percent
        return self.segments[-1].grade_percent

    def segment_progress_at(
        self,
        distance_m: float,
        loop: bool = True,
    ) -> tuple[int, int, float, float, float]:
        if not self.segments:
            return 1, 1, 0.0, 1.0, 0.0

        total_distance = self.total_distance_m
        if total_distance <= 0:
            return 1, len(self.segments), 0.0, 1.0, 0.0

        cursor = max(0.0, distance_m)
        if loop:
            cursor %= total_distance
        else:
            cursor = min(cursor, total_distance)

        covered = 0.0
        for index, segment in enumerate(self.segments, start=1):
            start = covered
            covered += segment.distance_m
            if cursor <= covered or index == len(self.segments):
                into_segment = min(max(0.0, cursor - start), segment.distance_m)
                progress = into_segment / segment.distance_m if segment.distance_m > 0 else 0.0
                return index, len(self.segments), into_segment, segment.distance_m, progress

        last = self.segments[-1]
        return len(self.segments), len(self.segments), last.distance_m, last.distance_m, 1.0

    def elevation_points(self) -> list[tuple[float, float]]:
        distance = 0.0
        elevation = 0.0
        points = [(distance, elevation)]
        for segment in self.segments:
            distance += segment.distance_m
            elevation += segment.distance_m * segment.grade_percent / 100.0
            points.append((distance, elevation))
        return points


def default_route_segments() -> list[RouteSegment]:
    return [
        RouteSegment(400.0, 0.0),
        RouteSegment(300.0, 2.0),
        RouteSegment(250.0, 5.0),
        RouteSegment(300.0, -2.0),
        RouteSegment(450.0, 0.5),
    ]


def default_route_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "route.json"


def load_route(path: Path | None = None) -> RouteProfile:
    target = path or default_route_path()
    if not target.exists():
        return RouteProfile()
    return RouteProfile.from_dict(json.loads(target.read_text(encoding="utf-8")))


def save_route(route: RouteProfile, path: Path | None = None) -> None:
    target = path or default_route_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(route.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
