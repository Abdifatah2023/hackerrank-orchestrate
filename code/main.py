"""Deletion-audit agent entry point.

    python code/main.py                     # audit all requests -> ./output.csv
    python code/main.py --validate FILE     # validate an existing output file
    python code/main.py --sample            # run on the 32 sample requests
    python code/main.py --debug-resource account_5684 [--before 2026-09-10T00:00:00Z]
    python code/main.py --debug-identifier someone@example.com

The pipeline is still a placeholder (every case COMPLETE with no steps); the
output is validated against the contract. The --debug-* flags dump what the
evidence store holds for a resource or identifier.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audit.dataset import OUTPUT_CSV, REQUESTS_CSV, SAMPLE_REQUESTS_CSV, load_citable_ids, load_requests, parse_time
from audit.emit import validate_output, write_output
from audit.ingest import build_store
from audit.observations import EMAIL, EMAIL_HASH, EMAIL_RE, HASH_RE, PHONE, USER, USER_RE, item_time
from audit.pipeline import audit_case


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requests", type=Path, help=f"request CSV (default: {REQUESTS_CSV.name})")
    parser.add_argument("--output", type=Path, help="output CSV (default: ./output.csv, or sample_predictions.csv with --sample)")
    parser.add_argument("--sample", action="store_true", help="use dataset/sample_deletion_requests.csv")
    parser.add_argument("--validate", type=Path, metavar="FILE", help="only validate FILE against the request CSV")
    parser.add_argument("--debug-resource", action="append", default=[], metavar="RESOURCE_ID")
    parser.add_argument("--debug-identifier", action="append", default=[], metavar="VALUE")
    parser.add_argument("--before", metavar="TIMESTAMP", help="with --debug-*: hide evidence captured after this time")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.debug_resource or args.debug_identifier:
        debug(args.debug_resource, args.debug_identifier, args.before)
        return 0

    requests_path = args.requests or (SAMPLE_REQUESTS_CSV if args.sample else REQUESTS_CSV)
    requests = load_requests(requests_path)

    if args.validate:
        output_path = args.validate
    else:
        output_path = args.output or (OUTPUT_CSV.with_name("sample_predictions.csv") if args.sample else OUTPUT_CSV)
        write_output([audit_case(r) for r in requests], output_path)
        print(f"wrote {len(requests)} rows to {output_path}")

    errors = validate_output(output_path, requests, load_citable_ids())
    for error in errors:
        print(f"INVALID {error}", file=sys.stderr)
    print(f"{output_path}: {'valid' if not errors else f'{len(errors)} contract violation(s)'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
