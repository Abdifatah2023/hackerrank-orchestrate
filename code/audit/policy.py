"""Policy engine: required treatment per category and the steps a resource needs (§3.6).

The category -> action table is read from `dataset/deletion_rules.csv`; only
the two recreation risks (data flows, backup restore) and the operation
names are fixed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .dataset import DATASET_DIR, read_csv
from .state import (
    ABSENT,
    ACTIVE,
    BACKUP_ARCHIVE,
    CLEAN,
    DATA_FLOW,
    EXPOSED,
    PRESENT,
    RESTORABLE,
    Assessment,
)

DELETION_RULES_CSV = DATASET_DIR / "deletion_rules.csv"


@lru_cache(maxsize=1)
def required_actions() -> dict[str, str]:
    """data_category -> DELETE | REDACT | ANONYMIZE | RETAIN."""
    return {r["data_category"]: r["required_action"] for r in read_csv(DELETION_RULES_CSV)}


@dataclass(frozen=True)
class ResourcePlan:
    """The operations one resource needs; `finding` is None when it is compliant."""

    finding: str | None
    remediation: str | None
    verification: str | None


_NOTHING = ResourcePlan(None, None, None)


def plan_resource(a: Assessment, always_verify: bool = False) -> ResourcePlan:
    """Steps for one resource. `always_verify` keeps a verification even when nothing is known (the account)."""
    if a.category == DATA_FLOW:
        if a.state == ACTIVE:
            return ResourcePlan("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED")
        return ResourcePlan(None, None, "VERIFY_DATA_FLOW_DISABLED") if a.state else _NOTHING
    if a.category == BACKUP_ARCHIVE:
        if a.state == RESTORABLE:
            return ResourcePlan("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED")
        return ResourcePlan(None, None, "VERIFY_RESTORE_BLOCKED") if a.state and a.state != ABSENT else _NOTHING

    action = required_actions().get(a.category, "DELETE")
    if action == "RETAIN":
        return ResourcePlan(None, None, "VERIFY_RECORD_PRESENT") if a.state != ABSENT else _NOTHING
    if action == "DELETE":
        if a.state in (PRESENT, EXPOSED):
            return ResourcePlan("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT")
        return ResourcePlan(None, None, "VERIFY_RECORD_ABSENT") if always_verify else _NOTHING
    verify = "VERIFY_RECORD_REDACTED" if action == "REDACT" else "VERIFY_RECORD_ANONYMIZED"
    if a.state == EXPOSED:
        fix = "REDACT_RECORD" if action == "REDACT" else "ANONYMIZE_RECORD"
        return ResourcePlan("REPORT_EXPOSED_PII", fix, verify)
    if a.state == CLEAN:
        return ResourcePlan(None, None, verify)
    return _NOTHING


def is_violation(a: Assessment) -> bool:
    return plan_resource(a).finding is not None
