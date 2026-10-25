"""PDF and screenshot evidence as free-text documents.

PDFs carry embedded text (pypdf). Screenshots need a vision model; their
extracted text is read from `code/cache/extractions/<sha256>.json`, which the
media-extraction step (roadmap milestone 8) writes. Until then a screenshot is
ingested as an empty, `extracted=False` document.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

from ..dataset import DATASET_DIR, REPO_ROOT
from ..observations import RESOURCE_ID_RE, Document, EvidenceFile, extract_identifiers

EXTRACTION_CACHE = REPO_ROOT / "code" / "cache" / "extractions"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _document(evidence: EvidenceFile, text: str, extracted: bool) -> Document:
    return Document(
        evidence_id=evidence.evidence_id,
        captured_at=evidence.captured_at,
        kind=evidence.kind,
        text=text,
        extracted=extracted,
        resource_ids=frozenset(RESOURCE_ID_RE.findall(text)),
        identifiers=extract_identifiers({}, text),
    )


def ingest_pdf(evidence: EvidenceFile) -> Document:
    reader = PdfReader(DATASET_DIR / evidence.path)
    return _document(evidence, "\n".join(page.extract_text() or "" for page in reader.pages), True)


def ingest_image(evidence: EvidenceFile) -> Document:
    cached = EXTRACTION_CACHE / f"{file_sha256(DATASET_DIR / evidence.path)}.json"
    if cached.exists():
        return _document(evidence, json.loads(cached.read_text(encoding="utf-8"))["text"], True)
    return _document(evidence, "", False)
