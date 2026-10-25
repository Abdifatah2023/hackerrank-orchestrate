"""Transcribe screenshot evidence into the extraction cache read by ingestion.

    python code/extract_media.py            # extract screenshots missing from the cache
    python code/extract_media.py --force    # re-extract every screenshot

Each PNG under dataset/evidence/images/ is sent once to Claude with a JSON
schema; the result is stored as code/cache/extractions/<sha256 of image>.json
with the screen's text and one record per resource row it shows. Needs the
`anthropic` package and ANTHROPIC_API_KEY (environment or .env). Token usage
is printed for the usage report.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys

from audit import llm
from audit.dataset import DATASET_DIR
from audit.ingest.documents import EXTRACTION_CACHE, file_sha256

SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "resource_ref": {"type": "string"},
                    "system_id": {"type": "string"},
                    "observed_at": {"type": ["string", "null"]},
                    "fields": {"type": "object"},
                },
                "required": ["resource_ref", "system_id", "observed_at", "fields"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["text", "records"],
    "additionalProperties": False,
}

PROMPT = """This is a screenshot of an internal admin console of an online shop, captured as audit evidence.

Return:
- text: a faithful plain-text transcription of everything on the screen (titles, table rows, detail panels, activity).
- records: one entry per data record the screen shows (customer profile, search document, connector, export,
  support ticket, backup archive, security event, analytics profile, file object, customer reference...):
  - resource_ref: the record's id exactly as shown (e.g. account_5762, data_flow_1121, or a file name or customer
    key when no id is shown);
  - system_id: the owning system (account_service, search_service, integration_service, export_service,
    support_platform, backup_service, security_service, analytics_platform, object_storage, identity_service,
    marketing_platform);
  - observed_at: ISO-8601 UTC time the screen shows for that record's state, or null;
  - fields: only what is visible, using these names when they apply: full_name, display_name, email, phone,
    customer_reference / user_id / subject_reference, requester_name, requester_email, owner_email,
    customer_email, file_name, uploaded_by, enabled (connector running), restore_allowed (archive can be
    restored; true unless a block is shown), download_available, account_status, result.
Do not guess values that are not on the screen."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="re-extract images that are already cached")
    args = parser.parse_args(argv)
    if not llm.available():
        print("ANTHROPIC_API_KEY or the anthropic package is missing; nothing extracted", file=sys.stderr)
        return 1

    EXTRACTION_CACHE.mkdir(parents=True, exist_ok=True)
    for image in sorted((DATASET_DIR / "evidence" / "images").glob("*.png")):
        target = EXTRACTION_CACHE / f"{file_sha256(image)}.json"
        if target.exists() and not args.force:
            continue
        data = base64.standard_b64encode(image.read_bytes()).decode("utf-8")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
            {"type": "text", "text": PROMPT},
        ]
        result = llm.call_json("vision", content, SCHEMA, max_tokens=8000)
        record = {
            "source_file": f"evidence/images/{image.name}",
            "extractor": {"provider": llm.PROVIDER, "model": llm.MODEL, "method": "code/extract_media.py"},
            **result,
        }
        target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"extracted {image.name}: {len(result['records'])} record(s)")
    print(json.dumps(llm.USAGE.summary(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
