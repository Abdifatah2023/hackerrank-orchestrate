"""Per-case audit pipeline (see code/ARCHITECTURE.md §3)."""

from __future__ import annotations

from .contract import COMPLETE
from .dataset import Request
from .emit import CaseResult


def audit_case(request: Request) -> CaseResult:
    # Placeholder until the pipeline in code/ARCHITECTURE.md §3 is built.
    return CaseResult(case_id=request.case_id, deletion_status=COMPLETE, steps=[])
