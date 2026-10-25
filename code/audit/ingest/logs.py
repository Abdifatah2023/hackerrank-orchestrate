"""Parse the evidence logs into flat key/value lines.

Every log family is a timestamp followed by one of six layouts:

    logfmt     level=INFO service=x trace_id=t event=e key=value ...
    bracket    [DEBUG] [service] [trace] event: key="value" ...
    pipe       | DEBUG | service | trace | event | key="value" ...
    json       {"service": "x", "trace_id": "t", "event": "e", ...}
    syslog     <15>1 node service trace - - level=DEBUG event="e" ...
    cloud      requestId=t level=DEBUG function=service message="key=\\"value\\" ..."

All are normalized to a dict with `service`, `trace_id`, `event` plus the
line's own keys; the literal string "null" becomes None.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..dataset import DATASET_DIR, parse_time
from ..observations import EvidenceFile, Observation, extract_identifiers, presence, system_for

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(.*)$")
_KV = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
_BRACKET = re.compile(r"^\[(\w+)\]\s+\[([^\]]+)\]\s+\[([^\]]+)\]\s+(\w+):\s*(.*)$")
_PIPE = re.compile(r"^\|?\s*(\w+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\w+)\s*\|\s*(.*)$")
_SYSLOG = re.compile(r"^<\d+>\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+-\s+-\s+(.*)$")

# Keys that name a trace or the emitting service across the layouts.
_TRACE_KEYS = ("trace_id", "requestId")
_SERVICE_KEYS = ("service", "function", "logger")

# Keys a trace's "started" line uses for what it searched for. They are
# shared with every line of the same trace, so a completed lookup (which
# carries resource_id but not the key) is linked to the identifier queried.
CONTEXT_KEYS = ("lookup_key", "match_key", "subject_key", "subject_ref", "scan_key", "owner_key", "correlation_key")


@dataclass(frozen=True)
class LogLine:
    line_no: int
    timestamp: datetime
    fields: dict[str, str | None]

    @property
    def service(self) -> str | None:
        return self.fields.get("service")

    @property
    def trace_id(self) -> str | None:
        return self.fields.get("trace_id")

    @property
    def event(self) -> str | None:
        return self.fields.get("event")


def _value(raw: str) -> str | None:
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return None if raw == "null" else raw


def parse_kv(text: str) -> dict[str, str | None]:
    return {k: _value(v) for k, v in _KV.findall(text)}


def parse_line(body: str) -> dict[str, str | None]:
    """Normalize one log line body (after the timestamp) to a flat dict."""
    if body.startswith("{"):
        fields = {k: (None if v in (None, "null") else str(v)) for k, v in json.loads(body).items()}
    elif m := _BRACKET.match(body):
        level, service, trace, event, rest = m.groups()
        fields = {"level": level, "service": service, "trace_id": trace, "event": event, **parse_kv(rest)}
    elif m := _PIPE.match(body):
        level, service, trace, event, rest = m.groups()
        fields = {"level": level, "service": service, "trace_id": trace, "event": event, **parse_kv(rest)}
    elif m := _SYSLOG.match(body):
        _node, service, trace, rest = m.groups()
        fields = {"service": service, "trace_id": trace, **parse_kv(rest)}
    else:
        fields = parse_kv(body)
        if not fields.get("level") and (head := body.split(" ", 1)[0]).isupper():
            fields["level"] = head
        if "message" in fields and "=" in (fields["message"] or ""):
            fields.update(parse_kv(fields.pop("message")))

    for key in _TRACE_KEYS:
        if fields.get(key) and not fields.get("trace_id"):
            fields["trace_id"] = fields[key]
    for key in _SERVICE_KEYS:
        if fields.get(key) and not fields.get("service"):
            fields["service"] = fields[key]
    return fields


def read_log(path: Path) -> list[LogLine]:
    lines = []
    with path.open(encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            if m := _TS.match(raw.rstrip("\n")):
                lines.append(LogLine(line_no, parse_time(m.group(1)), parse_line(m.group(2))))
    return lines


def ingest_log(evidence: EvidenceFile) -> list[Observation]:
    """One observation per log line that names a resource, enriched with its trace's context keys."""
    lines = read_log(DATASET_DIR / evidence.path)
    context: dict[str, dict[str, str]] = {}
    for line in lines:
        if line.trace_id:
            ctx = context.setdefault(line.trace_id, {})
            for key in CONTEXT_KEYS:
                if line.fields.get(key) and key not in ctx:
                    ctx[key] = line.fields[key]

    observations = []
    for line in lines:
        resource_id = line.fields.get("resource_id")
        if not resource_id:
            continue
        fields = {**context.get(line.trace_id or "", {}), **line.fields}
        observations.append(
            Observation(
                evidence_id=evidence.evidence_id,
                captured_at=evidence.captured_at,
                observed_at=line.timestamp,
                system_id=system_for(resource_id, line.fields.get("system") or line.service),
                resource_id=resource_id,
                source=f"{line.service}/{line.event}",
                copy="replica:support" if line.service == "support_replica" else "",
                exists=presence(line.fields),
                fields=fields,
                identifiers=extract_identifiers(fields),
                locator=f"line {line.line_no}",
            )
        )
    return observations
