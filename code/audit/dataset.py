"""Paths and loaders for the participant-facing files under dataset/."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"
REQUESTS_CSV = DATASET_DIR / "deletion_requests.csv"
SAMPLE_REQUESTS_CSV = DATASET_DIR / "sample_deletion_requests.csv"
SAMPLE_OUTPUT_CSV = DATASET_DIR / "sample_output.csv"
EVIDENCE_FILES_CSV = DATASET_DIR / "evidence_files.csv"
SYSTEM_ACTIVITY_CSV = DATASET_DIR / "system_activity.csv"
USER_AUDIT_LOG_CSV = DATASET_DIR / "user_audit_log.csv"
OUTPUT_CSV = REPO_ROOT / "output.csv"


def parse_time(value: str) -> datetime:
    """Parse the dataset's ISO-8601 UTC timestamps (`2026-07-16T08:00:00Z`)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@dataclass(frozen=True)
class Request:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


def load_requests(path: Path = REQUESTS_CSV) -> list[Request]:
    return [
        Request(
            case_id=row["case_id"],
            email=row["email"],
            requested_at=parse_time(row["requested_at"]),
            reported_status=row["reported_status"],
            reported_at=parse_time(row["reported_at"]),
            audit_at=parse_time(row["audit_at"]),
            request_text=row["request_text"],
        )
        for row in read_csv(path)
    ]


def load_citable_ids() -> dict[str, datetime]:
    """Every id a step may cite (`file_id`, `activity_id`, `audit_id`) with its timestamp."""
    ids = {r["file_id"]: parse_time(r["captured_at"]) for r in read_csv(EVIDENCE_FILES_CSV)}
    ids.update({r["activity_id"]: parse_time(r["event_time"]) for r in read_csv(SYSTEM_ACTIVITY_CSV)})
    ids.update({r["audit_id"]: parse_time(r["event_time"]) for r in read_csv(USER_AUDIT_LOG_CSV)})
    return ids
