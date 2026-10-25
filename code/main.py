"""Deletion-audit agent entry point.

    python code/main.py                     # audit all requests -> ./output.csv
    python code/main.py --validate FILE     # validate an existing output file
    python code/main.py --sample            # run on the 32 sample requests
    python code/main.py --debug-resource account_5684 [--before 2026-09-10T00:00:00Z]
    python code/main.py --debug-identifier someone@example.com
    python code/main.py --explain case_12   # intent, identity, linked resources, states, steps

Runs the full audit pipeline (intent -> identity -> discovery -> state at
audit_at -> policy -> planner, see code/ARCHITECTURE.md) and validates the
output against the contract, exiting 1 on any violation. The run is
deterministic and makes no model calls unless a request text is ambiguous and
an API key is configured; model usage is printed at the end. --explain shows
how a case was decided; the --debug-* flags dump what the evidence store holds
for a resource or identifier.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from audit import llm
from audit.dataset import OUTPUT_CSV, REQUESTS_CSV, SAMPLE_REQUESTS_CSV, load_citable_ids, load_requests, parse_time
from audit.emit import validate_output, write_output
from audit.ingest import build_store
from audit.observations import EMAIL, EMAIL_HASH, EMAIL_RE, HASH_RE, PHONE, USER, USER_RE, item_time
from audit.pipeline import audit_requests, run_audits


def describe_item(item) -> str:
    when = f"{item_time(item):%Y-%m-%dT%H:%M:%SZ}"
    kind = type(item).__name__
    if kind == "Activity":
        return (
            f"{when}  {item.activity_id:14} activity  {item.event_type} {item.source_system}->{item.target_system} "
            f"{item.resource_id} [{item.reported_result}] {item.details}"
        )
    if kind == "Document":
        return f"{when}  {item.evidence_id:14} {item.kind:9} mentions {sorted(item.resource_ids)} ids={sorted(item.identifiers)}"
    noise = {"level", "instance", "pod", "region", "duration_ms", "attempt", "metrics", "recent_activity", "trace_id"}
    fields = {k: v for k, v in item.fields.items() if k not in noise and v not in (None, "")}
    copy = f" copy={item.copy}" if item.copy else ""
    return (
        f"{when}  {item.evidence_id:14} {item.source}{copy} exists={item.exists} "
        f"(file {item.captured_at:%Y-%m-%dT%H:%M:%SZ}, {item.locator}) {fields}"
    )


def debug(resources: list[str], identifiers: list[str], before: str | None) -> None:
    store = build_store()
    cutoff = parse_time(before) if before else None
    for rid in resources:
        items = store.for_resource(rid, cutoff)
        print(f"== resource {rid}: {len(items)} item(s)")
        for item in items:
            print("  " + describe_item(item))
    for value in identifiers:
        kind = (
            EMAIL if EMAIL_RE.fullmatch(value) else
            EMAIL_HASH if HASH_RE.fullmatch(value) else
            USER if USER_RE.fullmatch(value) else PHONE
        )
        items = store.with_identifier(kind, value, cutoff)
        print(f"== {kind} {value}: {len(items)} item(s)")
        for item in items:
            print("  " + describe_item(item))


def explain(case_ids: list[str]) -> None:
    requests = {r.case_id: r for r in load_requests(REQUESTS_CSV) + load_requests(SAMPLE_REQUESTS_CSV)}
    unknown = [c for c in case_ids if c not in requests]
    if unknown:
        raise SystemExit(f"unknown case id(s): {', '.join(unknown)}")
    for audit in run_audits([requests[c] for c in case_ids]):
        r, cr = audit.request, audit.resources
        print(f"== {r.case_id}  reported {r.reported_status} at {r.reported_at:%Y-%m-%dT%H:%MZ}, audit_at {r.audit_at:%Y-%m-%dT%H:%MZ}")
        print(f"  intent: {'deletion' if audit.intent.authorized else 'NOT_APPLICABLE'} ({audit.intent.reason})")
        if cr:
            ident = cr.identity
            print(f"  identity: {ident.user_id}, {len(ident.identifiers)} identifiers ({len(ident.historical)} historical)")
            print(f"  account: {cr.account_id} via {cr.account_activity.activity_id if cr.account_activity else '-'}")
            for note in cr.notes:
                print(f"  note: {note}")
            for line in audit.plan.explanation:
                print(f"  {line}")
        print(f"  => {audit.plan.result.deletion_status}")
        for s in audit.plan.result.steps:
            print(f"     {s['step_no']:2} {s['phase']:12} {s['operation']:26} {s['system_id']}/{s['resource_id']} {s['evidence_id']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requests", type=Path, help=f"request CSV (default: {REQUESTS_CSV.name})")
    parser.add_argument("--output", type=Path, help="output CSV (default: ./output.csv, or sample_predictions.csv with --sample)")
    parser.add_argument("--sample", action="store_true", help="use dataset/sample_deletion_requests.csv")
    parser.add_argument("--validate", type=Path, metavar="FILE", help="only validate FILE against the request CSV")
    parser.add_argument("--debug-resource", action="append", default=[], metavar="RESOURCE_ID")
    parser.add_argument("--debug-identifier", action="append", default=[], metavar="VALUE")
    parser.add_argument("--before", metavar="TIMESTAMP", help="with --debug-*: hide evidence captured after this time")
    parser.add_argument("--explain", action="append", default=[], metavar="CASE_ID", help="show how a case was decided")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.explain:
        explain(args.explain)
        return 0
    if args.debug_resource or args.debug_identifier:
        debug(args.debug_resource, args.debug_identifier, args.before)
        return 0

    requests_path = args.requests or (SAMPLE_REQUESTS_CSV if args.sample else REQUESTS_CSV)
    requests = load_requests(requests_path)

    if args.validate:
        output_path = args.validate
    else:
        output_path = args.output or (OUTPUT_CSV.with_name("sample_predictions.csv") if args.sample else OUTPUT_CSV)
        write_output(audit_requests(requests), output_path)
        print(f"wrote {len(requests)} rows to {output_path}")
        usage = llm.USAGE.summary()
        calls = sum(m["calls"] for m in usage["models"].values())
        print(f"model calls this run: {calls} (cache hits {usage['cache_hits']}) {json.dumps(usage['models'])}")

    errors = validate_output(output_path, requests, load_citable_ids())
    for error in errors:
        print(f"INVALID {error}", file=sys.stderr)
    print(f"{output_path}: {'valid' if not errors else f'{len(errors)} contract violation(s)'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
