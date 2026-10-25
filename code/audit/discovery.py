"""Resource discovery: which resources belong to each case (code/ARCHITECTURE.md §3.4).

Two sources of linkage:

1. Direct: an observation, activity, or document carries one of the subject's
   current or historical identifiers (email, email hash, phone, user_id).
   Ownership does not change over time, so every file is used here; the
   `audit_at` cutoff is applied later, when state is judged and evidence cited.
2. The deletion burst: the deletion worker processes a day's requests in one
   batch shortly before `reported_at`. The account `DELETE_RECORD` it submits
   for a case lands 0-1.5 h before `reported_at` and its `reported_result`
   mirrors `reported_status` (SUCCESS / PARTIAL / FAILED). Anonymized records
   carry no identifiers at all, so the burst's `ANONYMIZE_RECORD` actions are
   their only link. Bursts are shared by several cases, so accounts and
   anonymized records are assigned across all cases at once.

Every resource ever tied by evidence to some other user and not to the
subject is excluded (decoy guard).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from .dataset import Request
from .identity import Identity, owners_of
from .observations import NAME, Activity, EvidenceStore, Observation

ACCOUNT_SYSTEM = "account_service"
STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
ACCOUNT_WINDOW = (timedelta(hours=-1.5), timedelta(hours=0.25))
TIGHT_WINDOW = (timedelta(minutes=-15), timedelta(minutes=15))
POLICY_WINDOW = (timedelta(hours=-1.5), timedelta(hours=0))

# Why a resource was linked to a case, strongest first.
DIRECT, BURST_ACCOUNT, BURST_ANONYMIZED = "direct", "burst_account", "burst_anonymized"


@dataclass
class CaseResources:
    case_id: str
    identity: Identity
    account_id: str | None = None
    account_activity: Activity | None = None
    resources: dict[str, set[str]] = field(default_factory=dict)  # resource_id -> reasons
    notes: list[str] = field(default_factory=list)

    def add(self, resource_id: str, reason: str) -> None:
        self.resources.setdefault(resource_id, set()).add(reason)


def resource_owners(store: EvidenceStore) -> dict[str, set[str]]:
    """resource_id -> user ids whose identifiers appear alongside it in any evidence."""
    owners: dict[str, set[str]] = defaultdict(set)

    def own(resource_ids, identifiers) -> None:
        users = set()
        for ident in identifiers:
            if ident[0] != NAME:
                users |= owners_of(ident)
        for rid in resource_ids:
            owners[rid] |= users

    for o in store.observations:
        own([o.resource_id], o.identifiers)
    for a in store.activities:
        own([a.resource_id], a.identifiers)
    for d in store.documents:
        own(d.resource_ids, d.identifiers)
    return dict(owners)


def direct_links(store: EvidenceStore, identity: Identity) -> set[str]:
    linked: set[str] = set()
    for kind, value in identity.identifiers:
        for item in store.with_identifier(kind, value):
            if rid := getattr(item, "resource_id", None):
                linked.add(rid)
            linked.update(getattr(item, "resource_ids", ()))
    return linked


def _in_window(activity: Activity, request: Request, window: tuple[timedelta, timedelta]) -> bool:
    delta = activity.event_time - request.reported_at
    return window[0] <= delta <= window[1]


def discover(
    store: EvidenceStore,
    requests: list[Request],
    identities: dict[str, Identity],
) -> dict[str, CaseResources]:
    """Link resources to every authorized case. `requests` must hold every case sharing the bursts."""
    owners = resource_owners(store)
    cases = {r.case_id: CaseResources(r.case_id, identities[r.case_id]) for r in requests}

    def foreign(resource_id: str, user_id: str | None) -> bool:
        users = owners.get(resource_id, set())
        return bool(users) and user_id not in users

    for r in requests:
        cr = cases[r.case_id]
        for rid in sorted(direct_links(store, cr.identity)):
            cr.add(rid, DIRECT)

    _assign_accounts(store, requests, cases, foreign)
    return cases


def _assign_accounts(store, requests, cases, foreign) -> None:
    """Give each case the account its burst deleted, matched in time order within (reported_at, status)."""
    deletes = sorted(
        (
            a
            for a in store.activities
            if a.event_type == "DELETE_RECORD" and a.source_system == "deletion_worker" and a.system_id == ACCOUNT_SYSTEM
        ),
        key=lambda a: a.event_time,
    )
    taken: set[str] = set()

    # Pass 1: an in-window delete of an account the subject's own evidence names.
    for r in requests:
        cr = cases[r.case_id]
        result = STATUS_RESULT.get(r.reported_status)
        own = [
            a
            for a in deletes
            if a.resource_id in cr.resources and a.reported_result == result and _in_window(a, r, ACCOUNT_WINDOW)
        ]
        if own:
            choice = min(own, key=lambda a: abs(a.event_time - r.reported_at))
            cr.account_id, cr.account_activity = choice.resource_id, choice
            taken.add(choice.resource_id)

    # Pass 2: remaining cases, grouped by burst and status, matched in order.
    groups: dict[tuple, list[Request]] = defaultdict(list)
    for r in requests:
        if cases[r.case_id].account_id is None:
            groups[(r.reported_at, r.reported_status)].append(r)
    for (reported_at, status), group in sorted(groups.items()):
        group.sort(key=lambda r: _case_number(r.case_id))
        result = STATUS_RESULT.get(status)
        pool = [
            a
            for a in deletes
            if a.resource_id not in taken
            and a.reported_result == result
            and _in_window(a, group[0], ACCOUNT_WINDOW)
            and not all(foreign(a.resource_id, cases[r.case_id].identity.user_id) for r in group)
        ]
        for r, a in zip(group, pool):
            cr = cases[r.case_id]
            cr.account_id, cr.account_activity = a.resource_id, a
            taken.add(a.resource_id)

    # Pass 3: a delete within minutes of reported_at whatever its result (the
    # worker's final report can disagree with the workflow status).
    for r in requests:
        cr = cases[r.case_id]
        if cr.account_id is not None:
            continue
        near = [
            a
            for a in deletes
            if a.resource_id not in taken
            and _in_window(a, r, TIGHT_WINDOW)
            and not foreign(a.resource_id, cr.identity.user_id)
        ]
        if near:
            choice = min(near, key=lambda a: abs(a.event_time - r.reported_at))
            cr.account_id, cr.account_activity = choice.resource_id, choice
            taken.add(choice.resource_id)
            cr.notes.append(f"account from a {choice.reported_result} delete next to reported_at")

    # Pass 4: no burst delete at all (e.g. a request handled outside the batch
    # worker): fall back to the subject's own account seen latest before audit_at.
    for r in requests:
        cr = cases[r.case_id]
        if cr.account_id is not None:
            continue
        seen = [
            (item.captured_at, rid)
            for rid in cr.resources
            if rid.startswith("account_") and rid not in taken
            for item in store.for_resource(rid, r.audit_at)
            if isinstance(item, Observation)
        ]
        if seen:
            cr.account_id = max(seen)[1]
            taken.add(cr.account_id)
            cr.notes.append("account from the subject's own evidence (no burst delete found)")

    # Flag choices that an unclaimed delete of the same kind could have taken.
    for r in requests:
        cr = cases[r.case_id]
        result = STATUS_RESULT.get(r.reported_status)
        spare = [
            a.resource_id
            for a in deletes
            if a.resource_id not in taken and a.reported_result == result and _in_window(a, r, ACCOUNT_WINDOW)
        ]
        if cr.account_id is None:
            cr.notes.append("no account delete found for this request")
        elif spare and cr.account_activity is not None and cr.account_id not in cr.resources:
            cr.notes.append(f"account choice ambiguous; unclaimed alternatives {sorted(spare)}")

    for cr in cases.values():
        if cr.account_id:
            cr.add(cr.account_id, BURST_ACCOUNT)


def assign_anonymized(
    store: EvidenceStore,
    requests: list[Request],
    cases: dict[str, CaseResources],
    eligible: set[str],
) -> None:
    """Hand each burst's anonymized records to its eligible cases, in case-number order.

    An anonymized record carries no identifier, so only its burst links it to
    a case. The pipeline decides eligibility (the case has no analytics record
    of its own and its policy actions ran); when a burst has more eligible cases
    than records the earliest cases are served and the ambiguity is noted.
    """
    owners = resource_owners(store)
    actions = sorted(
        (
            a
            for a in store.activities
            if a.event_type == "ANONYMIZE_RECORD" and a.source_system == "deletion_worker" and a.reported_result == "SUCCESS"
        ),
        key=lambda a: a.event_time,
    )
    linked = {rid for cr in cases.values() for rid in cr.resources}
    bursts: dict = defaultdict(list)
    for r in requests:
        bursts[r.reported_at].append(r)
    taken: set[str] = set()
    for _, group in sorted(bursts.items()):
        pool = [
            a
            for a in actions
            if a.resource_id not in taken
            and a.resource_id not in linked
            and not owners.get(a.resource_id)
            and _in_window(a, group[0], POLICY_WINDOW)
        ]
        needy = sorted((r for r in group if r.case_id in eligible), key=lambda r: _case_number(r.case_id))
        if not pool or not needy:
            continue
        if len(pool) != len(needy):
            for r in needy:
                cases[r.case_id].notes.append(
                    f"anonymized burst ambiguous: {len(pool)} record(s) for {len(needy)} eligible case(s)"
                )
        for r, a in zip(needy, pool):
            cases[r.case_id].add(a.resource_id, BURST_ANONYMIZED)
            taken.add(a.resource_id)


def _case_number(case_id: str) -> int:
    tail = case_id.rpartition("_")[2]
    return int(tail) if tail.isdigit() else 0
