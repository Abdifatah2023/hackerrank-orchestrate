"""Load SQL snapshot exports into in-memory SQLite and emit observations and coverage.

Two schemas occur:
  - typed inventory tables (`customer_profiles`, `connector_subscriptions`, ...)
    plus a `snapshot_scope(origin_system, captured_at)` table naming the
    systems the snapshot covered;
  - generic `<base>_<copy>(resource_id, origin_system, collection_name,
    record_json, updated_at)` tables, one per primary / replica copy, for
    comparing replication drift.
"""

from __future__ import annotations

import json
import re
import sqlite3

from ..dataset import DATASET_DIR, parse_time
from ..observations import Coverage, EvidenceFile, Observation, extract_identifiers, flatten_fields, system_for

# Typed tables that do not say which system owns them.
TABLE_SYSTEMS = {
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}

_COPY_SUFFIX = re.compile(r"^(?P<base>.+?)_(?P<copy>primary|read_replica|reporting_replica|failover_replica|replica_\w+)$")


def split_copy(table: str) -> tuple[str, str]:
    """`customer_data_replica_eu_west` -> (`customer_data`, `replica:eu_west`)."""
    m = _COPY_SUFFIX.match(table)
    if not m:
        return table, ""
    copy = m.group("copy")
    if copy == "primary":
        return m.group("base"), "primary"
    name = copy.removeprefix("replica_").removesuffix("_replica")
    return m.group("base"), f"replica:{name}"


def ingest_sql(evidence: EvidenceFile) -> tuple[list[Observation], list[Coverage]]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((DATASET_DIR / evidence.path).read_text(encoding="utf-8"))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    scope: dict[str, str] = {}
    if "snapshot_scope" in tables:
        scope = {r["origin_system"]: r["captured_at"] for r in conn.execute("SELECT * FROM snapshot_scope")}

    observations: list[Observation] = []
    # (system, table) -> resource ids, for coverage
    captured: dict[tuple[str, str], set[str]] = {}
    systems_in_file: set[str] = set()

    for table in tables:
        if table == "snapshot_scope":
            continue
        base, copy = split_copy(table)
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            fields = dict(row)
            if "record_json" in fields:
                fields = flatten_fields({**json.loads(fields.pop("record_json")), **fields})
            resource_id = fields["resource_id"]
            stated = fields.get("origin_system") or TABLE_SYSTEMS.get(base)
            system_id = system_for(resource_id, stated)
            systems_in_file.add(system_id)
            # A snapshot row exists at capture time; updated_at is only its last change.
            observed = scope.get(system_id)
            observations.append(
                Observation(
                    evidence_id=evidence.evidence_id,
                    captured_at=evidence.captured_at,
                    observed_at=parse_time(observed) if observed else evidence.captured_at,
                    system_id=system_id,
                    resource_id=resource_id,
                    source=fields.get("collection_name") or table,
                    copy=copy,
                    exists=True,
                    fields=fields,
                    identifiers=extract_identifiers(fields),
                    locator=f"table {table}",
                )
            )
            captured.setdefault((system_id, table), set()).add(resource_id)

    # Coverage: inventory snapshots name their systems in snapshot_scope (even
    # with zero rows); copy tables cover every system seen anywhere in the file.
    coverage = []
    for table in tables:
        if table == "snapshot_scope":
            continue
        base, copy = split_copy(table)
        if scope:
            system = TABLE_SYSTEMS.get(base)
            covered = {system} if system in scope else set()
        elif base in TABLE_SYSTEMS:
            covered = {TABLE_SYSTEMS[base]}
        else:
            covered = systems_in_file
        for system in sorted(covered):
            at = scope.get(system)
            coverage.append(
                Coverage(
                    evidence_id=evidence.evidence_id,
                    captured_at=evidence.captured_at,
                    observed_at=parse_time(at) if at else evidence.captured_at,
                    system_id=system,
                    source=table,
                    copy=copy,
                    resource_ids=frozenset(captured.get((system, table), set())),
                )
            )
    conn.close()
    return observations, coverage
