"""PDF and screenshot evidence.

PDFs carry embedded text (pypdf). Screenshots need a vision model: their
extraction is read from `code/cache/extractions/<sha256>.json` (written by
`code/extract_media.py`), holding the screen's text plus structured records
(`resource_ref`, `system_id`, optional `observed_at`, visible `fields`).

Business documents and screens often name a resource by an alias (a file
name, a statement number, a customer key). The data catalog's
`LINK_RESOURCE_IDENTITY` events state those aliases, e.g.
"Data catalog linked export_1842 (NST-2026-0718-1842) to ...", so aliases
are resolved through them.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader

from ..dataset import DATASET_DIR, REPO_ROOT, parse_time
from ..identity import owners_of
from ..observations import (
    RESOURCE_ID_RE,
    USER,
    USER_RE,
    Activity,
    Document,
    EvidenceFile,
    Observation,
    extract_identifiers,
    presence,
    system_for,
)

EXTRACTION_CACHE = REPO_ROOT / "code" / "cache" / "extractions"
_ALIAS = re.compile(r"\b(?:linked|confirmed) (\w+) \(([^)]+)\)")


def resource_aliases(activities: list[Activity]) -> dict[str, str]:
    """alias -> resource_id, from the data catalog's link events."""
    aliases = {}
    for a in activities:
        if a.event_type == "LINK_RESOURCE_IDENTITY":
            for resource_id, alias in _ALIAS.findall(a.details):
                if alias != resource_id:
                    aliases[alias] = resource_id
    return aliases


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _document(evidence: EvidenceFile, text: str, extracted: bool, aliases: dict[str, str], link: bool) -> Document:
    """`link=False` keeps the text for display only: a screen lists many people's rows,
    so its links come from the per-record observations instead."""
    mentioned = set(RESOURCE_ID_RE.findall(text))
    mentioned |= {
        rid
        for alias, rid in aliases.items()
        if not USER_RE.fullmatch(alias) and re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", text)
    }
    identifiers = extract_identifiers({}, text)
    # A register listing several customers cannot say which rows belong to whom.
    people = {value for kind, value in identifiers if kind == USER} | set().union(
        *(owners_of(i) for i in identifiers if i[0] != USER)
    )
    if len(people) > 1:
        link = False
    return Document(
        evidence_id=evidence.evidence_id,
        captured_at=evidence.captured_at,
        kind=evidence.kind,
        text=text,
        extracted=extracted,
        resource_ids=frozenset(mentioned) if link else frozenset(),
        identifiers=identifiers if link else frozenset(),
    )


def ingest_pdf(evidence: EvidenceFile, aliases: dict[str, str]) -> Document:
    reader = PdfReader(DATASET_DIR / evidence.path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return _document(evidence, text, True, aliases, link=True)


def ingest_image(evidence: EvidenceFile, aliases: dict[str, str]) -> tuple[Document, list[Observation]]:
    cached = EXTRACTION_CACHE / f"{file_sha256(DATASET_DIR / evidence.path)}.json"
    if not cached.exists():
        return _document(evidence, "", False, aliases, link=False), []
    extraction = json.loads(cached.read_text(encoding="utf-8"))
    observations = []
    for i, record in enumerate(extraction.get("records", [])):
        ref = record["resource_ref"]
        resource_id = ref if RESOURCE_ID_RE.fullmatch(ref) else aliases.get(ref)
        if resource_id is None:
            continue  # a screen row we cannot tie to a resource
        fields = {"resource_id": resource_id, **record.get("fields", {})}
        exists = presence(fields)
        observed = record.get("observed_at")
        observations.append(
            Observation(
                evidence_id=evidence.evidence_id,
                captured_at=evidence.captured_at,
                observed_at=parse_time(observed) if observed else evidence.captured_at,
                system_id=system_for(resource_id, record.get("system_id")),
                resource_id=resource_id,
                source=f"screenshot:{Path(evidence.path).stem}",
                copy="",
                exists=True if exists is None else exists,
                fields=fields,
                identifiers=extract_identifiers(fields),
                locator=f"screen record {i}",
            )
        )
    return _document(evidence, extraction.get("text", ""), True, aliases, link=False), observations
