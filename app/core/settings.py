from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.core.exam_controller import EXAM_MODE_TIME, MAX_RIDERS, MIN_RIDERS

EXAM_LAYOUT_AUTO = "auto"
EXAM_LAYOUT_MODES = {EXAM_LAYOUT_AUTO, "1", "2", "3", "4"}


@dataclass(slots=True)
class AppSettings:
    exam_mode: str = EXAM_MODE_TIME
    duration_seconds: int = 60
    rider_count: int = 4
    bike_weight_kg: float = 10.0
    mock_mode: bool = False
    push_grade: bool = True
    drafting_enabled: bool = False
    exam_layout_mode: str = EXAM_LAYOUT_AUTO

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        layout_mode = str(data.get("exam_layout_mode", EXAM_LAYOUT_AUTO))
        if layout_mode not in EXAM_LAYOUT_MODES:
            layout_mode = EXAM_LAYOUT_AUTO
        return cls(
            exam_mode=str(data.get("exam_mode", EXAM_MODE_TIME)),
            duration_seconds=max(1, int(data.get("duration_seconds", 60))),
            rider_count=min(
                MAX_RIDERS,
                max(MIN_RIDERS, int(data.get("rider_count", 4))),
            ),
            bike_weight_kg=min(30.0, max(5.0, float(data.get("bike_weight_kg", 10.0)))),
            mock_mode=bool(data.get("mock_mode", False)),
            push_grade=bool(data.get("push_grade", True)),
            drafting_enabled=bool(data.get("drafting_enabled", False)),
            exam_layout_mode=layout_mode,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "exam_mode": self.exam_mode,
            "duration_seconds": self.duration_seconds,
            "rider_count": self.rider_count,
            "bike_weight_kg": round(self.bike_weight_kg, 1),
            "mock_mode": self.mock_mode,
            "push_grade": self.push_grade,
            "drafting_enabled": self.drafting_enabled,
            "exam_layout_mode": self.exam_layout_mode,
        }


def default_settings_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or default_settings_path()
    if not target.exists():
        return AppSettings()
    return AppSettings.from_dict(json.loads(target.read_text(encoding="utf-8")))


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    target = path or default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
