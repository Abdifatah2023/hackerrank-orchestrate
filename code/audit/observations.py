"""Unified evidence model: every parser emits these records into one `EvidenceStore`.

Ingest stays faithful to the files: it normalizes shapes, identifiers, and the
"is this record present?" signal, and leaves policy and state judgements to
later stages (code/ARCHITECTURE.md §3.5-3.6).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

# Owning system per resource-id prefix. Matches every system_id in the sample
# answers; evidence that names a copy location (replica cluster, archive) is
# kept separately in `Observation.source`.
SYSTEM_BY_PREFIX = {
    "account_": "account_service",
    "analytics_": "analytics_platform",
    "audit_record_": "security_service",
    "backup_": "backup_service",
    "cache_": "cache_service",
    "data_flow_": "integration_service",
    "email_delivery_": "email_delivery_service",
    "export_": "export_service",
    "legal_hold_": "legal_service",
    "marketing_": "marketing_platform",
    "object_": "object_storage",
    "search_": "search_service",
    "support_": "support_platform",
    "tombstone_": "identity_service",
}

RESOURCE_ID_RE = re.compile(
    r"\b(?:" + "|".join(p.rstrip("_") for p in sorted(SYSTEM_BY_PREFIX, key=len, reverse=True)) + r")_\d+\b"
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
USER_RE = re.compile(r"\buser_\d+\b")
PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,}\d")

NAME_FIELDS = {"full_name", "display_name", "requester_name", "name", "customer_name"}

# Identifier kinds, in the order identity resolution will use them.
EMAIL, EMAIL_HASH, PHONE, NAME, USER = "email", "email_hash", "phone", "name", "user"


def system_for(resource_id: str, stated: str | None = None) -> str:
    for prefix, system in SYSTEM_BY_PREFIX.items():
        if resource_id.startswith(prefix):
            return system
    return stated or "unknown"


def normalize_identifier(kind: str, value: str) -> str:
    value = value.strip()
    if kind == PHONE:
        return re.sub(r"\D", "", value)
    if kind == NAME:
        return " ".join(value.casefold().split())
    return value.lower()


def extract_identifiers(fields: dict, text: str | None = None) -> frozenset[tuple[str, str]]:
    """Every (kind, normalized value) identifier in a record's fields and optional free text."""
    found: set[tuple[str, str]] = set()

    def scan(value: str, key: str = "") -> None:
        found.update((EMAIL, normalize_identifier(EMAIL, m)) for m in EMAIL_RE.findall(value))
        found.update((EMAIL_HASH, m.lower()) for m in HASH_RE.findall(value))
        found.update((USER, m.lower()) for m in USER_RE.findall(value))
        found.update((PHONE, normalize_identifier(PHONE, m)) for m in PHONE_RE.findall(value))
        if "phone" in key:
            found.add((PHONE, normalize_identifier(PHONE, value)))
        if key in NAME_FIELDS:
            found.add((NAME, normalize_identifier(NAME, value)))

    for key, value in fields.items():
        if isinstance(value, str) and value:
            scan(value, key)
    if text:
        scan(text)
    return frozenset(i for i in found if i[1])


_ABSENT_RESULTS = {"NOT_FOUND", "MISS", "ABSENT", "DELETED"}


def presence(fields: dict) -> bool | None:
    """Whether a record says the resource exists: True, False, or None if it does not say."""
    status = fields.get("status_code", fields.get("http_status"))
    if status is not None:
        return str(status) != "404"
    rows = fields.get("rows")
    if rows is not None and str(rows).isdigit():
        return int(rows) > 0
    for key in ("total", "total_hits"):
        if (v := fields.get(key)) is not None and str(v).isdigit():
            return int(v) > 0
    result = fields.get("result")
    if isinstance(result, str):
        if result.upper() in _ABSENT_RESULTS:
            return False
        if result.lower() == "hit" or result.isupper():
            return True
    return None


@dataclass(frozen=True)
class EvidenceFile:
    evidence_id: str
    path: str  # relative to dataset/
    origin: str
    captured_at: datetime

    @property
    def kind(self) -> str:
        return self.path.rsplit(".", 1)[-1].lower()


@dataclass(frozen=True)
class Observation:
    """One resource as seen in one place in one evidence file."""

    evidence_id: str
    captured_at: datetime  # file capture time: the citation cutoff
    observed_at: datetime  # row / snapshot / log-line time (falls back to captured_at)
    system_id: str  # owning system (see SYSTEM_BY_PREFIX)
    resource_id: str
    source: str  # where it was seen: table, collection, or log service/event
    copy: str  # "primary", "replica:<name>", or "" when the file has a single copy
    exists: bool | None
    fields: dict = field(hash=False, compare=False)
    identifiers: frozenset[tuple[str, str]] = frozenset()
    locator: str = ""  # line number / table, for debugging


@dataclass(frozen=True)
class Coverage:
    """A table or collection captured whole by a snapshot file.

    A resource of `system_id` missing from `resource_ids` was not in that
    capture, which is how a snapshot proves a record ABSENT.
    """

    evidence_id: str
    captured_at: datetime  # file capture time: the citation cutoff
    observed_at: datetime  # when this table/system was captured
    system_id: str
    source: str
    copy: str
    resource_ids: frozenset[str]


@dataclass(frozen=True)
class Activity:
    """One `system_activity.csv` row. A claim, never proof of state."""

    activity_id: str
    event_time: datetime
    event_type: str
    source_system: str
    target_system: str
    system_id: str  # owning system of resource_id
    resource_id: str
    reported_result: str
    details: str
    mentioned_resource_ids: tuple[str, ...]
    identifiers: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class Document:
    """A PDF or screenshot: free text plus the resource ids and identifiers it mentions."""

    evidence_id: str
    captured_at: datetime
    kind: str  # "pdf" | "png"
    text: str
    extracted: bool  # False for a screenshot with no cached extraction yet
    resource_ids: frozenset[str]
    identifiers: frozenset[tuple[str, str]]


@dataclass
class EvidenceStore:
    files: dict[str, EvidenceFile]
    observations: list[Observation]
    coverage: list[Coverage]
    activities: list[Activity]
    documents: list[Document]
    _by_resource: dict[str, list] = field(default_factory=dict, repr=False)
    _by_identifier: dict[tuple[str, str], list] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        by_resource: dict[str, list] = defaultdict(list)
        by_identifier: dict[tuple[str, str], list] = defaultdict(list)
        for o in self.observations:
            by_resource[o.resource_id].append(o)
            for ident in o.identifiers:
                by_identifier[ident].append(o)
        for a in self.activities:
            for rid in {a.resource_id, *a.mentioned_resource_ids}:
                by_resource[rid].append(a)
            for ident in a.identifiers:
                by_identifier[ident].append(a)
        for d in self.documents:
            for rid in d.resource_ids:
                by_resource[rid].append(d)
            for ident in d.identifiers:
                by_identifier[ident].append(d)
        self._by_resource = dict(by_resource)
        self._by_identifier = dict(by_identifier)

    def for_resource(self, resource_id: str, cutoff: datetime | None = None) -> list:
        """Observations, activities, and documents mentioning a resource, oldest first."""
        return _visible(self._by_resource.get(resource_id, []), cutoff)

    def with_identifier(self, kind: str, value: str, cutoff: datetime | None = None) -> list:
        """Everything carrying one identifier, oldest first."""
        return _visible(self._by_identifier.get((kind, normalize_identifier(kind, value)), []), cutoff)

    def coverage_for(self, system_id: str, cutoff: datetime | None = None) -> list[Coverage]:
        return _visible([c for c in self.coverage if c.system_id == system_id], cutoff)

    def resource_ids(self) -> set[str]:
        return set(self._by_resource)


def item_time(item) -> datetime:
    return getattr(item, "observed_at", None) or getattr(item, "event_time", None) or item.captured_at


def item_cutoff_time(item) -> datetime:
    """The time an item becomes citable: file capture time, or event time for activities."""
    return getattr(item, "captured_at", None) or item.event_time


def _visible(items: list, cutoff: datetime | None) -> list:
    if cutoff is not None:
        items = [i for i in items if item_cutoff_time(i) <= cutoff]
    return sorted(items, key=item_time)
