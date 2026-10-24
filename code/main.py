"""Deletion-audit agent entry point.

    python code/main.py                     # audit all requests -> ./output.csv
    python code/main.py --validate FILE     # validate an existing output file
    python code/main.py --sample            # run on the 32 sample requests

Milestone 0 (contract skeleton): every case is emitted as COMPLETE with no
steps, and the result is validated against the output contract.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audit.contract import COMPLETE
from audit.dataset import OUTPUT_CSV, REQUESTS_CSV, SAMPLE_REQUESTS_CSV, Request, load_citable_ids, load_requests
from audit.emit import CaseResult, validate_output, write_output


def audit_case(request: Request) -> CaseResult:
    # Placeholder until the pipeline in code/ARCHITECTURE.md §3 is built.
    return CaseResult(case_id=request.case_id, deletion_status=COMPLETE, steps=[])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requests", type=Path, help=f"request CSV (default: {REQUESTS_CSV.name})")
    parser.add_argument("--output", type=Path, help="output CSV (default: ./output.csv, or sample_predictions.csv with --sample)")
    parser.add_argument("--sample", action="store_true", help="use dataset/sample_deletion_requests.csv")
    parser.add_argument("--validate", type=Path, metavar="FILE", help="only validate FILE against the request CSV")
    args = parser.parse_args(argv)

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
