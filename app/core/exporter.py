from __future__ import annotations

import csv
from pathlib import Path

from app.core.exam_controller import ExamController


SUMMARY_FIELDS = [
    "exam_id",
    "rider_slot",
    "rider_name",
    "device_name",
    "device_address",
    "weight_kg",
    "bike_weight_kg",
    "exam_mode",
    "drafting_enabled",
    "duration_seconds",
    "route_distance_m",
    "finish_time_seconds",
    "average_power",
    "max_power",
    "average_heart_rate",
    "max_heart_rate",
    "simulated_distance_m",
    "average_speed_kph",
    "valid_time",
    "dropout_time",
    "status",
    "start_time",
    "end_time",
]

SAMPLE_FIELDS = [
    "exam_id",
    "rider_slot",
    "timestamp",
    "elapsed_seconds",
    "current_power",
    "simulated_speed_kph",
    "simulated_distance_m",
    "grade_percent",
    "segment_index",
    "segment_progress",
    "draft_aero_multiplier",
    "draft_gap_m",
    "draft_leader_slot",
    "draft_riders_ahead",
    "draft_savings_watts",
    "heart_rate_bpm",
    "status",
]


def export_exam_csv(controller: ExamController, export_root: Path | None = None) -> Path:
    if not controller.exam_id:
        raise ValueError("没有可导出的考试数据")

    root = export_root or Path(__file__).resolve().parents[2] / "exports"
    target_dir = root / controller.exam_id
    target_dir.mkdir(parents=True, exist_ok=True)

    summary_path = target_dir / "summary.csv"
    samples_path = target_dir / "samples.csv"

    with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(controller.summary_rows())

    with samples_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=SAMPLE_FIELDS)
        writer.writeheader()
        writer.writerows(controller.sample_rows())

    return target_dir
