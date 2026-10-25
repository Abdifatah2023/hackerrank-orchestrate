"""Evidence ingestion: parse every evidence file into a unified `EvidenceStore`."""

from __future__ import annotations

from functools import lru_cache

from ..dataset import EVIDENCE_FILES_CSV, parse_time, read_csv
from ..observations import EvidenceFile, EvidenceStore
from .activity import load_activities
from .documents import ingest_image, ingest_pdf, resource_aliases
from .logs import ingest_log
from .records import ingest_json, ingest_text
from .sql import ingest_sql


def load_manifest() -> dict[str, EvidenceFile]:
    return {
        r["file_id"]: EvidenceFile(r["file_id"], r["file_path"], r["origin"], parse_time(r["captured_at"]))
        for r in read_csv(EVIDENCE_FILES_CSV)
    }


@lru_cache(maxsize=1)
def build_store() -> EvidenceStore:
    files = load_manifest()
    activities = load_activities()
    aliases = resource_aliases(activities)
    observations, coverage, documents = [], [], []
    for evidence in files.values():
        kind = evidence.kind
        if kind == "sql":
            obs, cov = ingest_sql(evidence)
            observations += obs
            coverage += cov
        elif kind == "json":
            obs, cov = ingest_json(evidence)
            observations += obs
            coverage += cov
        elif kind == "log":
            observations += ingest_log(evidence)
        elif kind == "txt":
            observations += ingest_text(evidence)
        elif kind == "pdf":
            documents.append(ingest_pdf(evidence, aliases))
        elif kind == "png":
            document, obs = ingest_image(evidence, aliases)
            documents.append(document)
            observations += obs
        else:
            raise ValueError(f"{evidence.evidence_id}: unsupported evidence type {evidence.path}")
    return EvidenceStore(files, observations, coverage, activities, documents)
