"""Write output.csv and validate any output file against the contract."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .contract import (
    COMPLETE,
    FINDING,
    INCOMPLETE,
    NOT_APPLICABLE,
    OPERATION_PHASE,
    OPERATION_STATE,
    OUTPUT_COLUMNS,
    PHASES,
    STATUSES,
    STEP_FIELDS,
    VERIFICATION,
    case_sort_key,
    serialize_steps,
    step_sort_key,
)
from .dataset import Request


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    deletion_status: str
    steps: list[dict]


def write_output(results: list[CaseResult], path: Path) -> None:
    rows = sorted(results, key=lambda r: case_sort_key(r.case_id))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        for r in rows:
            writer.writerow([r.case_id, r.deletion_status, serialize_steps(r.steps)])


def validate_output(
    path: Path,
    requests: list[Request],
    citable_ids: dict[str, datetime] | None = None,
) -> list[str]:
    """Return every contract violation in `path`; an empty list means valid.

    With `citable_ids`, also check that each cited evidence id exists and was
    captured at or before the case's `audit_at`.
    """
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        rows = list(reader)

    if tuple(header or ()) != OUTPUT_COLUMNS:
        return [f"header must be {','.join(OUTPUT_COLUMNS)}, got {header}"]

    audit_at = {r.case_id: r.audit_at for r in requests}
    case_ids = [row[0] if row else "" for row in rows]
    if len(rows) != len(requests):
        errors.append(f"expected {len(requests)} data rows, got {len(rows)}")
    if len(set(case_ids)) != len(case_ids):
        errors.append("duplicate case_id rows")
    if missing := sorted(audit_at.keys() - set(case_ids), key=case_sort_key):
        errors.append(f"missing cases: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")
    if case_ids != sorted(case_ids, key=case_sort_key):
        errors.append("rows are not sorted by case_id")

    for line_no, row in enumerate(rows, start=2):
        if len(row) != len(OUTPUT_COLUMNS):
            errors.append(f"line {line_no}: expected {len(OUTPUT_COLUMNS)} columns, got {len(row)}")
            continue
        case_id, status, steps_json = row
        if case_id not in audit_at:
            errors.append(f"{case_id}: not in the request file")
        errors.extend(
            f"{case_id}: {e}"
            for e in _validate_case(status, steps_json, audit_at.get(case_id), citable_ids)
        )
    return errors


def _validate_case(
    status: str,
    steps_json: str,
    audit_at: datetime | None,
    citable_ids: dict[str, datetime] | None,
) -> list[str]:
    if status not in STATUSES:
        return [f"invalid deletion_status {status!r}"]
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return [f"steps_json is not valid JSON ({e})"]
    if not isinstance(steps, list):
        return ["steps_json is not a JSON array"]
    if status == NOT_APPLICABLE and steps:
        return ["NOT_APPLICABLE must have steps_json=[]"]

    errors: list[str] = []
    prev_key = None
    for i, step in enumerate(steps, start=1):
        where = f"step {i}"
        if not isinstance(step, dict) or set(step) != set(STEP_FIELDS):
            errors.append(f"{where}: must have exactly the fields {', '.join(STEP_FIELDS)}")
            continue
        if step["step_no"] != i:
            errors.append(f"{where}: step_no is {step['step_no']!r}, expected {i}")
        phase, operation = step["phase"], step["operation"]
        if phase not in PHASES:
            errors.append(f"{where}: invalid phase {phase!r}")
            continue
        if OPERATION_PHASE.get(operation) != phase:
            errors.append(f"{where}: operation {operation!r} is not allowed in {phase}")
            continue
        if step["state"] != OPERATION_STATE[operation]:
            errors.append(f"{where}: {operation} requires state {OPERATION_STATE[operation]}, got {step['state']!r}")
        if not all(isinstance(step[k], str) and step[k] for k in ("system_id", "resource_id")):
            errors.append(f"{where}: system_id and resource_id must be non-empty strings")
            continue

        key = step_sort_key(phase, operation, step["system_id"], step["resource_id"])
        if prev_key is not None and key <= prev_key:
            errors.append(f"{where}: out of order or duplicate (phase, operation, system_id, resource_id)")
        prev_key = key

        evidence_id = step["evidence_id"]
        if not isinstance(evidence_id, str):
            errors.append(f"{where}: evidence_id must be a string")
        elif status == COMPLETE and phase != VERIFICATION:
            errors.append(f"{where}: a COMPLETE case may only contain VERIFICATION steps")
        elif status == COMPLETE and not evidence_id:
            errors.append(f"{where}: COMPLETE verification must cite evidence")
        elif status == INCOMPLETE and phase != FINDING and evidence_id:
            errors.append(f"{where}: INCOMPLETE {phase.lower()} steps must use evidence_id=\"\"")
        elif status == INCOMPLETE and phase == FINDING and not evidence_id:
            errors.append(f"{where}: findings must cite evidence")
        elif evidence_id and citable_ids is not None:
            captured = citable_ids.get(evidence_id)
            if captured is None:
                errors.append(f"{where}: unknown evidence_id {evidence_id!r}")
            elif audit_at is not None and captured > audit_at:
                errors.append(f"{where}: {evidence_id} is dated after audit_at")

    if status == INCOMPLETE and not any(s.get("phase") == FINDING for s in steps if isinstance(s, dict)):
        errors.append("INCOMPLETE case has no FINDING step")
    return errors
