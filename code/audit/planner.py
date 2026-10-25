"""Planner: assessments -> status and ordered steps (code/ARCHITECTURE.md §3.7).

status = INCOMPLETE if any resource violates policy, else COMPLETE.
FINDING       one per violation, citing the file that shows it.
REMEDIATION   one per finding (DISABLE / BLOCK / DELETE / REDACT / ANONYMIZE).
VERIFICATION  the account, every retained record, every remediated resource,
              and every resource already in its required final state.
COMPLETE cases cite the evidence proving each verified state; INCOMPLETE
remediation and verification describe future work and cite nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import COMPLETE, INCOMPLETE, NOT_APPLICABLE, Step, number_steps
from .discovery import CaseResources
from .emit import CaseResult
from .observations import EvidenceStore
from .policy import is_violation, plan_resource
from .state import ABSENT, Assessment, absence_proof


@dataclass
class CasePlan:
    result: CaseResult
    explanation: list[str] = field(default_factory=list)


def plan_case(
    case_id: str,
    authorized: bool,
    resources: CaseResources | None,
    assessments: dict[str, Assessment],
    store: EvidenceStore,
    audit_at,
    reported_at=None,
) -> CasePlan:
    if not authorized or resources is None:
        return CasePlan(CaseResult(case_id, NOT_APPLICABLE, []), ["request does not authorize deletion"])

    findings: list[Step] = []
    remediation: list[Step] = []
    verification: list[tuple[Step, str | None]] = []  # step + evidence for a COMPLETE outcome
    explanation: list[str] = []

    for rid in sorted(resources.resources):
        a = assessments[rid]
        is_account = rid == resources.account_id
        if a.resource_id.startswith("account_") and not is_account and not is_violation(a):
            continue  # an older, already-deleted account of the same person
        plan = plan_resource(a, always_verify=is_account)
        explanation.append(f"{a.system_id}/{rid}: {a.category} state={a.state} via {sorted(resources.resources[rid])}")
        if plan.finding:
            findings.append(Step(a.system_id, rid, plan.finding, a.evidence_id or ""))
            remediation.append(Step(a.system_id, rid, plan.remediation))
        if plan.verification:
            proof = a.evidence_id if a.state and not plan.finding else None
            verification.append((Step(a.system_id, rid, plan.verification), proof))

    # Records proven absent only by omission: cite the snapshot that left them
    # out, preferring a file this case already cites (its audit snapshot).
    cited = {proof for _, proof in verification if proof}
    for i, (step, proof) in enumerate(verification):
        if proof is None and step.operation == "VERIFY_RECORD_ABSENT" and assessments[step.resource_id].state in (None, ABSENT):
            proof = absence_proof(store, step.resource_id, step.system_id, audit_at, since=reported_at, prefer=cited)
            if proof is None and step.resource_id == resources.account_id and resources.account_activity is not None:
                proof = resources.account_activity.activity_id
            verification[i] = (step, proof)

    if findings:
        steps = findings + remediation + [step for step, _ in verification]
        return CasePlan(CaseResult(case_id, INCOMPLETE, number_steps(steps)), explanation)
    proven = [Step(s.system_id, s.resource_id, s.operation, proof) for s, proof in verification if proof]
    return CasePlan(CaseResult(case_id, COMPLETE, number_steps(proven)), explanation)
