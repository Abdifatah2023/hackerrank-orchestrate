"""Per-case audit pipeline (code/ARCHITECTURE.md §3).

intent -> identity -> discovery -> state at audit_at -> policy -> planner.

Deletion bursts are shared between cases, so discovery always runs over every
participant request (sample and evaluation) even when only some are audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .dataset import REQUESTS_CSV, SAMPLE_REQUESTS_CSV, Request, load_requests
from .discovery import CaseResources, assign_anonymized, discover
from .emit import CaseResult
from .identity import resolve
from .ingest import build_store
from . import llm
from .intent import Intent, classify
from .observations import system_for
from .planner import CasePlan, plan_case
from .policy import is_violation
from .state import ACCOUNT_TOMBSTONE, ANALYTICS_PROFILE, Assessment, assess

POLICY_ACTIONS = {"ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE"}


@dataclass
class CaseAudit:
    request: Request
    intent: Intent
    resources: CaseResources | None
    assessments: dict[str, Assessment]
    plan: CasePlan


def _context_requests(requests: list[Request]) -> list[Request]:
    by_id = {r.case_id: r for r in load_requests(SAMPLE_REQUESTS_CSV) + load_requests(REQUESTS_CSV)}
    by_id.update({r.case_id: r for r in requests})
    return list(by_id.values())


def run_audits(requests: list[Request]) -> list[CaseAudit]:
    store = build_store()
    context = _context_requests(requests)
    intents = {r.case_id: _intent(r.request_text) for r in context}
    authorized = [r for r in context if intents[r.case_id].authorized]
    identities = {r.case_id: resolve(r.email) for r in authorized}
    cases = discover(store, authorized, identities)

    def assess_case(r: Request) -> dict[str, Assessment]:
        cr = cases[r.case_id]
        return {rid: assess(store, rid, system_for(rid), r.audit_at, since=r.reported_at) for rid in cr.resources}

    # Anonymized records can only be placed once each case's own state is known.
    first = {r.case_id: assess_case(r) for r in authorized}
    eligible = {r.case_id for r in authorized if _wants_anonymized(r, cases[r.case_id], first[r.case_id], store)}
    assign_anonymized(store, authorized, cases, eligible)

    audits = []
    for r in requests:
        intent = intents[r.case_id]
        cr = cases.get(r.case_id)
        assessments = assess_case(r) if cr else {}
        plan = plan_case(r.case_id, intent.authorized, cr, assessments, store, r.audit_at, r.reported_at)
        audits.append(CaseAudit(r, intent, cr, assessments, plan))
    return audits


def _intent(text: str) -> Intent:
    """Rules first; a model decides only texts the rules are unsure about, when one is configured."""
    intent = classify(text)
    if intent.certain or not llm.available():
        return intent
    authorized, reason = llm.classify_intent(text)
    return Intent(authorized, f"model: {reason}")


def audit_requests(requests: list[Request]) -> list[CaseResult]:
    return [a.plan.result for a in run_audits(requests)]


def _wants_anonymized(r: Request, cr: CaseResources, assessments: dict[str, Assessment], store) -> bool:
    """A case whose burst anonymized a record for it: no analytics or tombstone of its own,
    and either nothing left to fix or policy actions that ran on its own resources."""
    if any(a.category in (ANALYTICS_PROFILE, ACCOUNT_TOMBSTONE) for a in assessments.values()):
        return False
    if not any(is_violation(a) for a in assessments.values()):
        return True
    window = (r.reported_at - timedelta(hours=1.5), r.reported_at)
    return any(
        act.event_type in POLICY_ACTIONS
        and act.reported_result == "SUCCESS"
        and window[0] <= act.event_time <= window[1]
        for rid in cr.resources
        for act in store.for_resource(rid)
        if hasattr(act, "event_type")
    )
