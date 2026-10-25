"""JSON record files and pipe-delimited text handovers.

The JSON families (service readback exports, snapshot bundles, ledgers,
analytics reports, export jobs) differ only in how records are nested, so one
walker handles them all: any object with a `resource_id` is a record, and the
containers above it supply the system, collection, and time context. A
container that names a `collection` and lists its `records` (possibly empty)
is a snapshot of that collection and also yields a `Coverage`.
"""

from __future__ import annotations

import json
import re

from ..dataset import DATASET_DIR, parse_time
from ..observations import (
    SYSTEM_BY_PREFIX,
    Coverage,
    EvidenceFile,
    Observation,
    extract_identifiers,
    presence,
    system_for,
)

_SYSTEM_KEYS = ("data_store", "system", "origin_system")
_CONTAINER_TIME_KEYS = ("captured_at", "exported_at", "observed_at", "recorded_at")
_RECORD_TIME_KEYS = ("observed_at", "recorded_at", "updated_at", "captured_at")
_KNOWN_SYSTEMS = set(SYSTEM_BY_PREFIX.values())


def _is_record_list(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, dict) and "resource_id" in x for x in value)


def _first(d: dict, keys: tuple[str, ...]):
    return next((d[k] for k in keys if isinstance(d.get(k), str) and d[k]), None)


class _JsonWalker:
    def __init__(self, evidence: EvidenceFile):
        self.evidence = evidence
        self.observations: list[Observation] = []
        self.coverage: list[Coverage] = []

    def walk(self, node, ctx: dict, path: str) -> None:
        if isinstance(node, list):
            for i, item in enumerate(node):
                self.walk(item, ctx, f"{path}[{i}]")
            return
        if not isinstance(node, dict):
            return
        ctx = {**ctx}
        if system := _first(node, _SYSTEM_KEYS):
            ctx["system"] = system
        if collection := _first(node, ("collection",)):
            ctx["collection"] = collection
        if time := _first(node, _CONTAINER_TIME_KEYS):
            ctx["time"] = time
        if "resource_id" in node:
            self.record(node, ctx, path)
            return
        if collection and isinstance(records := node.get("records"), list):
            self.snapshot(records, ctx)
        for key, value in node.items():
            if not isinstance(value, (dict, list)):
                continue
            child = {**ctx}
            if key in _KNOWN_SYSTEMS or key.endswith(("_service", "_platform")):
                child["system"] = key
            elif isinstance(value, list) and key not in ("records", "snapshots"):
                child["collection"] = key
            self.walk(value, child, f"{path}.{key}")

    def snapshot(self, records: list, ctx: dict) -> None:
        resource_ids = frozenset(r["resource_id"] for r in records if isinstance(r, dict) and "resource_id" in r)
        systems = {system_for(rid, ctx.get("system")) for rid in resource_ids} or {ctx.get("system")}
        if len(systems) != 1 or None in systems:
            return  # cannot tell which system an empty, unlabelled collection belongs to
        time = ctx.get("time")
        self.coverage.append(
            Coverage(
                evidence_id=self.evidence.evidence_id,
                captured_at=self.evidence.captured_at,
                observed_at=parse_time(time) if time else self.evidence.captured_at,
                system_id=systems.pop(),
                source=ctx["collection"],
                copy="",
                resource_ids=resource_ids,
            )
        )

    def record(self, node: dict, ctx: dict, path: str) -> None:
        resource_id = node["resource_id"]
        fields: dict = {}
        nested: list[dict] = []

        def flatten(d: dict, prefix: str = "") -> None:
            for key, value in d.items():
                if isinstance(value, dict):
                    flatten(value, f"{key}.")
                elif _is_record_list(value):
                    nested.extend(value)
                else:
                    fields[f"{prefix}{key}" if key in fields else key] = value

        flatten(node)
        # A search query's hit list repeats the queried document: merge it in.
        for child in nested:
            if child["resource_id"] == resource_id:
                flatten({k: v for k, v in child.items() if k != "resource_id"})
            else:
                self.record(child, ctx, f"{path}.hit")

        observed = _first(node, _RECORD_TIME_KEYS) or ctx.get("time")
        exists = presence(fields)
        self.observations.append(
            Observation(
                evidence_id=self.evidence.evidence_id,
                captured_at=self.evidence.captured_at,
                observed_at=parse_time(observed) if observed else self.evidence.captured_at,
                system_id=system_for(resource_id, ctx.get("system")),
                resource_id=resource_id,
                source=ctx.get("collection") or ctx.get("source") or "",
                copy="",
                exists=True if exists is None else exists,
                fields=fields,
                identifiers=extract_identifiers(fields),
                locator=path,
            )
        )


def ingest_json(evidence: EvidenceFile) -> tuple[list[Observation], list[Coverage]]:
    data = json.loads((DATASET_DIR / evidence.path).read_text(encoding="utf-8"))
    ctx = {"source": data.get("source") or data.get("origin") or ""} if isinstance(data, dict) else {}
    walker = _JsonWalker(evidence)
    walker.walk(data, ctx, "$")
    return walker.observations, walker.coverage


# - 2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE | NOT_FOUND | e.lindberg18@yahoo.com
_HANDOVER_ROW = re.compile(
    r"^\s*-\s*(?P<time>\S+)\s*\|\s*(?P<system>[\w-]+)\s*\|\s*(?P<resource_id>\w+)\s*\|"
    r"\s*(?P<record_type>[A-Z_]+)\s*\|\s*(?P<result>[A-Z_]+)\s*\|\s*(?P<subject_reference>.*?)\s*$"
)


def ingest_text(evidence: EvidenceFile) -> list[Observation]:
    observations = []
    lines = (DATASET_DIR / evidence.path).read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        if not (m := _HANDOVER_ROW.match(line)):
            continue
        fields = m.groupdict()
        observations.append(
            Observation(
                evidence_id=evidence.evidence_id,
                captured_at=evidence.captured_at,
                observed_at=parse_time(fields["time"]),
                system_id=system_for(fields["resource_id"], fields["system"]),
                resource_id=fields["resource_id"],
                source="handover",
                copy="",
                exists=presence(fields),
                fields=fields,
                identifiers=extract_identifiers(fields),
                locator=f"line {line_no}",
            )
        )
    return observations
