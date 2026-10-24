"""Output contract: statuses, phases, allowed operations, and step ordering.

Mirrors problem_statement.md / AGENTS.md §6.3-6.4. The operation tables are
listed in contract order, which is also the within-phase sort order.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

OUTPUT_COLUMNS = ("case_id", "deletion_status", "steps_json")

COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
NOT_APPLICABLE = "NOT_APPLICABLE"
STATUSES = (COMPLETE, INCOMPLETE, NOT_APPLICABLE)

FINDING = "FINDING"
REMEDIATION = "REMEDIATION"
VERIFICATION = "VERIFICATION"
PHASES = (FINDING, REMEDIATION, VERIFICATION)

# operation -> required state, grouped by phase in contract order.
OPERATIONS: dict[str, dict[str, str]] = {
    FINDING: {
        "REPORT_REMAINING_RECORD": "PRESENT",
        "REPORT_EXPOSED_PII": "PRESENT",
        "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
        "REPORT_RESTORE_RISK": "ACTIVE",
    },
    REMEDIATION: {
        "DISABLE_DATA_FLOW": "DISABLED",
        "BLOCK_RESTORE": "BLOCKED",
        "DELETE_RECORD": "ABSENT",
        "REDACT_RECORD": "REDACTED",
        "ANONYMIZE_RECORD": "ANONYMIZED",
    },
    VERIFICATION: {
        "VERIFY_RECORD_ABSENT": "ABSENT",
        "VERIFY_RECORD_PRESENT": "PRESENT",
        "VERIFY_RECORD_REDACTED": "REDACTED",
        "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
        "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
        "VERIFY_RESTORE_BLOCKED": "BLOCKED",
    },
}

OPERATION_PHASE = {op: phase for phase, ops in OPERATIONS.items() for op in ops}
OPERATION_STATE = {op: state for ops in OPERATIONS.values() for op, state in ops.items()}
OPERATION_RANK = {op: rank for ops in OPERATIONS.values() for rank, op in enumerate(ops)}
PHASE_RANK = {phase: rank for rank, phase in enumerate(PHASES)}

STEP_FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")


@dataclass(frozen=True)
class Step:
    """One unnumbered step; numbering happens in `number_steps`."""

    system_id: str
    resource_id: str
    operation: str
    evidence_id: str = ""

    @property
    def phase(self) -> str:
        return OPERATION_PHASE[self.operation]

    @property
    def state(self) -> str:
        return OPERATION_STATE[self.operation]


def step_sort_key(phase: str, operation: str, system_id: str, resource_id: str) -> tuple:
    """Phase, then contract operation order, then system_id, then resource_id."""
    return (PHASE_RANK[phase], OPERATION_RANK[operation], system_id, resource_id)


def number_steps(steps: list[Step]) -> list[dict]:
    """Sort steps into contract order and number them from 1."""
    ordered = sorted(steps, key=lambda s: step_sort_key(s.phase, s.operation, s.system_id, s.resource_id))
    return [
        {**asdict(s), "phase": s.phase, "state": s.state, "step_no": i}
        for i, s in enumerate(ordered, start=1)
    ]


def serialize_steps(steps: list[dict]) -> str:
    """Compact, key-sorted JSON so output is byte-for-byte deterministic."""
    return json.dumps(steps, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def case_sort_key(case_id: str) -> tuple:
    """Natural sort (`case_33` before `case_100`), matching the input file order."""
    prefix, _, number = case_id.rpartition("_")
    return (prefix, int(number)) if number.isdigit() else (case_id, -1)
