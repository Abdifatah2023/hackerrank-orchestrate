"""State reconstruction at `audit_at` (code/ARCHITECTURE.md §3.5).

Every observation captured at or before the cutoff is turned into a *reading*
of the resource (absent, present, exposed, clean, active, disabled,
restorable, blocked). Readings are grouped by copy (primary / each replica);
the latest reading per copy wins, and the worst copy decides the resource's
state, so a replica that still holds data is a violation even when the
primary is clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .ingest.logs import CONTEXT_KEYS
from .observations import (
    EMAIL,
    EMAIL_HASH,
    NAME,
    PHONE,
    USER,
    Coverage,
    Document,
    EvidenceStore,
    Observation,
    extract_identifiers,
)

# Data categories (deletion_rules.csv) plus the two recreation-risk kinds.
USER_PROFILE = "USER_PROFILE"
SECONDARY_IDENTIFIER = "SECONDARY_IDENTIFIER"
SEARCH_INDEX_ENTRY = "SEARCH_INDEX_ENTRY"
CACHE_ENTRY = "CACHE_ENTRY"
SUPPORT_MESSAGE = "SUPPORT_MESSAGE"
SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"
ANALYTICS_PROFILE = "ANALYTICS_PROFILE"
PRODUCT_TELEMETRY = "PRODUCT_TELEMETRY"
USER_EXPORT = "USER_EXPORT"
MARKETING_PROFILE = "MARKETING_PROFILE"
EMAIL_DELIVERY_LOG = "EMAIL_DELIVERY_LOG"
SECURITY_AUDIT_LOG = "SECURITY_AUDIT_LOG"
LEGAL_HOLD_RECORD = "LEGAL_HOLD_RECORD"
BACKUP_ARCHIVE = "BACKUP_ARCHIVE"
ACCOUNT_TOMBSTONE = "ACCOUNT_TOMBSTONE"
DATA_FLOW = "DATA_FLOW"

CATEGORY_BY_PREFIX = {
    "account_": USER_PROFILE,
    "analytics_": ANALYTICS_PROFILE,
    "audit_record_": SECURITY_AUDIT_LOG,
    "backup_": BACKUP_ARCHIVE,
    "cache_": CACHE_ENTRY,
    "data_flow_": DATA_FLOW,
    "email_delivery_": EMAIL_DELIVERY_LOG,
    "export_": USER_EXPORT,
    "legal_hold_": LEGAL_HOLD_RECORD,
    "marketing_": MARKETING_PROFILE,
    "object_": SUPPORT_ATTACHMENT,
    "search_": SEARCH_INDEX_ENTRY,
    "support_": SUPPORT_MESSAGE,
    "tombstone_": ACCOUNT_TOMBSTONE,
}

# Readings, from compliant to violating.
ABSENT, CLEAN, DISABLED, BLOCKED, PRESENT, EXPOSED, ACTIVE, RESTORABLE = (
    "absent", "clean", "disabled", "blocked", "present", "exposed", "active", "restorable",
)

_RESULT_READING = {
    "FOUND": PRESENT, "NOT_FOUND": ABSENT, "IDENTIFIABLE": EXPOSED, "ANONYMIZED": CLEAN,
    "REDACTED": CLEAN, "DISABLED": DISABLED, "BLOCKED": BLOCKED,
}
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}
# Fields that carry record content (as opposed to lookup keys or metadata).
_CONTENT_IDENTIFIER_FIELDS = {
    "email", "phone", "email_hash", "name", "full_name", "display_name", "requester_email", "requester_name",
    "requester_phone", "customer_email", "owner_email", "owner_phone", "message_text", "message_excerpt",
    "matched_value", "uploaded_by", "document", "profile",
}
_USER_REFERENCE_FIELDS = {"user_id", "customer_reference"}
_ATTACHMENT_HINTS = {"file_name", "uploaded_by", "file", "storage_class"}


def _flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    return True if text in _TRUE else False if text in _FALSE else None


def categorize(resource_id: str, observations: list[Observation], in_document: bool = False) -> str:
    """Policy category from the resource's prefix, refined by what the evidence shows.

    A support resource referenced by an uploaded document (PDF / screenshot of
    a file) is that document's attachment.
    """
    category = CATEGORY_BY_PREFIX.get(next((p for p in CATEGORY_BY_PREFIX if resource_id.startswith(p)), ""), "")
    for o in observations:
        stated = o.fields.get("record_type")
        if stated in (SUPPORT_ATTACHMENT, SECONDARY_IDENTIFIER, PRODUCT_TELEMETRY, CACHE_ENTRY, EMAIL_DELIVERY_LOG):
            return stated
    if category == SUPPORT_MESSAGE:
        if in_document:
            return SUPPORT_ATTACHMENT
        if any(_ATTACHMENT_HINTS & set(o.fields) or "attachment" in o.source for o in observations):
            return SUPPORT_ATTACHMENT
        if any(o.copy == "replica:support" for o in observations):
            return SECONDARY_IDENTIFIER  # legacy contact index copy: deleted outright
    if category == ANALYTICS_PROFILE and any(o.source.endswith("contact_lookup_completed") for o in observations):
        return SECONDARY_IDENTIFIER  # hash-keyed contact lookup row
    return category


def content_identifiers(o: Observation, include_user_reference: bool) -> frozenset:
    fields = {k: v for k, v in o.fields.items() if k in _CONTENT_IDENTIFIER_FIELDS and k not in CONTEXT_KEYS}
    found = {i for i in extract_identifiers(fields) if i[0] in (EMAIL, EMAIL_HASH, PHONE, NAME)}
    if include_user_reference and any(o.fields.get(k) for k in _USER_REFERENCE_FIELDS):
        found.add((USER, "reference"))
    return frozenset(found)


def reading(o: Observation, category: str) -> str | None:
    """What one observation says about the resource, or None if it says nothing."""
    f = o.fields
    event = f.get("event") or ""
    if event.endswith("_started"):
        return None
    if (result := f.get("result")) and isinstance(result, str) and result.upper() in _RESULT_READING:
        return _RESULT_READING[result.upper()]
    if result == "ACTIVE" or (isinstance(result, str) and result.upper() == "ACTIVE"):
        return RESTORABLE if category == BACKUP_ARCHIVE else ACTIVE
    if o.exists is False:
        return ABSENT

    if category == DATA_FLOW:
        enabled = _flag(f.get("enabled"))
        return None if enabled is None else ACTIVE if enabled else DISABLED
    if category == BACKUP_ARCHIVE:
        if (allowed := _flag(f.get("restore_allowed"))) is not None:
            return RESTORABLE if allowed else BLOCKED
        if f.get("decision") == "deny":
            return BLOCKED
        eligible, block = _flag(f.get("eligible")), _flag(f.get("deletion_block"))
        if eligible is not None:
            return RESTORABLE if eligible and not block else BLOCKED
        return None
    if category in (ANALYTICS_PROFILE, PRODUCT_TELEMETRY, SUPPORT_MESSAGE, ACCOUNT_TOMBSTONE, EMAIL_DELIVERY_LOG):
        if not (set(f) & (_CONTENT_IDENTIFIER_FIELDS | _USER_REFERENCE_FIELDS)):
            return PRESENT if o.exists else None
        user_ref = category in (ANALYTICS_PROFILE, PRODUCT_TELEMETRY)
        return EXPOSED if content_identifiers(o, user_ref) else CLEAN
    if category == USER_EXPORT:
        available = _flag(f.get("available", f.get("download_available")))
        if available is False:
            return ABSENT
    if category in (SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD):
        return PRESENT if o.exists is not False else ABSENT
    return PRESENT if o.exists else None


@dataclass(frozen=True)
class Reading:
    state: str
    evidence_id: str
    copy: str
    at: datetime  # observed time
    captured_at: datetime


@dataclass(frozen=True)
class Assessment:
    resource_id: str
    system_id: str
    category: str
    state: str | None  # worst current reading across copies; None when nothing is known
    evidence_id: str | None  # file proving `state`
    readings: tuple[Reading, ...]


def _copy_key(copy: str) -> str:
    return "primary" if copy in ("", "primary") else copy


_SEVERITY = {ABSENT: 0, CLEAN: 1, DISABLED: 1, BLOCKED: 1, PRESENT: 2, EXPOSED: 3, ACTIVE: 3, RESTORABLE: 3}


def assess(
    store: EvidenceStore, resource_id: str, system_id: str, cutoff: datetime, since: datetime | None = None
) -> Assessment:
    """State at `cutoff` from readings observed after `since` (the workflow's report).

    Readings from before the deletion workflow reported only show the expected
    pre-deletion state, so they are not evidence either way.
    """
    observations = [
        i
        for i in store.for_resource(resource_id, cutoff)
        if isinstance(i, Observation) and (since is None or i.observed_at > since)
    ]
    everything = store.for_resource(resource_id)
    category = categorize(
        resource_id,
        [i for i in everything if isinstance(i, Observation)],
        in_document=any(isinstance(i, Document) and i.kind == "pdf" for i in everything),
    )
    readings = []
    for o in observations:
        if (state := reading(o, category)) is not None:
            readings.append(Reading(state, o.evidence_id, _copy_key(o.copy), o.observed_at, o.captured_at))
    for d in store.for_resource(resource_id, cutoff):
        # A PDF mentioning the resource shows it exists; screenshots yield structured observations instead.
        if isinstance(d, Document) and d.kind == "pdf" and (since is None or d.captured_at > since):
            readings.append(Reading(PRESENT, d.evidence_id, "primary", d.captured_at, d.captured_at))

    latest: dict[str, Reading] = {}
    for r in sorted(readings, key=lambda r: (r.at, r.captured_at)):
        latest[r.copy] = r
    if not latest:
        return Assessment(resource_id, system_id, category, None, None, tuple(readings))
    worst = max(latest.values(), key=lambda r: (_SEVERITY[r.state], r.at, r.captured_at))
    return Assessment(resource_id, system_id, category, worst.state, worst.evidence_id, tuple(readings))


def absence_proof(
    store: EvidenceStore,
    resource_id: str,
    system_id: str,
    cutoff: datetime,
    since: datetime | None = None,
    prefer: set[str] = frozenset(),
) -> str | None:
    """A snapshot taken after `since` and captured by `cutoff` whose copy of the system's table lacks this resource.

    Files in `prefer` win; otherwise the latest snapshot does.
    """
    proofs: list[Coverage] = [
        c
        for c in store.coverage_for(system_id, cutoff)
        if resource_id not in c.resource_ids
        and c.copy in ("", "primary")
        and (since is None or c.observed_at > since)
    ]
    if not proofs:
        return None
    return max(proofs, key=lambda c: (c.evidence_id in prefer, c.captured_at, c.evidence_id)).evidence_id
