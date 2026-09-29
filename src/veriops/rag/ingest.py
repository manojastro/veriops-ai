"""Document ingestion: parse front matter → sanitise-scan → chunk → embed → store."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veriops.rag.chunking import chunk_markdown
from veriops.rag.embeddings import Embedder
from veriops.rag.store import KnowledgeDocument, KnowledgeStore, StoredChunk
from veriops.security.sanitizer import scan

logger = logging.getLogger(__name__)

DOCUMENT_TYPES = {
    "runbook",
    "service_doc",
    "architecture",
    "incident_report",
    "deployment_procedure",
    "database_guide",
    "rollback_procedure",
    "slo",
}
DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"


@dataclass
class IngestResult:
    document_id: str
    title: str
    chunks: int
    skipped: bool
    flagged_injection: bool
    findings: list[str]


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    meta: dict[str, str] = {}
    for line in raw[3:end].strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta, raw[end + 4 :].lstrip()


class DocumentIngestor:
    def __init__(self, store: KnowledgeStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    def ingest_text(
        self, content: str, metadata: dict[str, Any], *, source_path: str | None = None, force: bool = False
    ) -> IngestResult:
        document_type = metadata.get("document_type", "runbook")
        if document_type not in DOCUMENT_TYPES:
            raise ValueError(f"unsupported document_type {document_type!r}")
        title = metadata.get("title") or (content.splitlines()[0].lstrip("# ").strip() if content else "untitled")
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        doc_id = metadata.get("id") or f"doc-{content_hash[:12]}"
        findings = scan(content)
        if not force and self.store.has_hash(content_hash):
            return IngestResult(doc_id, title, 0, True, bool(findings), findings)
        chunks = chunk_markdown(content)
        vectors = self.embedder.embed([f"{title}. {c.heading}. {c.content}" for c in chunks]) if chunks else []
        document = KnowledgeDocument(
            id=doc_id,
            title=title,
            document_type=document_type,
            content=content,
            content_hash=content_hash,
            service=metadata.get("service") or None,
            environment=metadata.get("environment") or None,
            team=metadata.get("team") or None,
            severity=metadata.get("severity") or None,
            incident_ref=metadata.get("incident_id") or None,
            version=str(metadata.get("version", "1")),
            source_path=source_path,
            flagged_injection=bool(findings),
            metadata={k: v for k, v in metadata.items() if k not in {"title", "id"}} | {"injection_findings": findings},
        )
        stored = [
            StoredChunk(
                id=f"{doc_id}#{c.ordinal}",
                document_id=doc_id,
                ordinal=c.ordinal,
                heading=c.heading,
                content=c.content,
                embedding=v,
            )
            for c, v in zip(chunks, vectors, strict=True)
        ]
        self.store.upsert(document, stored)
        if findings:
            logger.warning("document %s flagged for prompt-injection patterns: %s", doc_id, findings)
        return IngestResult(doc_id, title, len(stored), False, bool(findings), findings)

    def ingest_file(self, path: Path, force: bool = False) -> IngestResult:
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        return self.ingest_text(body, meta, source_path=str(path), force=force)

    def ingest_directory(
        self, directory: Path = DEFAULT_KNOWLEDGE_DIR, *, include_adversarial: bool = False, force: bool = False
    ) -> list[IngestResult]:
        results = []
        for path in sorted(directory.rglob("*.md")):
            if "adversarial" in path.parts and not include_adversarial:
                continue
            results.append(self.ingest_file(path, force=force))
        return results
