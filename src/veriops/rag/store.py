"""Knowledge stores: PostgreSQL + pgvector (production) and in-memory (tests / offline tools)."""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from veriops.db.models import DocumentChunkRow, KnowledgeDocumentRow
from veriops.rag.embeddings import tokenize


@dataclass
class KnowledgeDocument:
    id: str
    title: str
    document_type: str
    content: str
    content_hash: str
    service: str | None = None
    environment: str | None = None
    team: str | None = None
    severity: str | None = None
    incident_ref: str | None = None
    version: str = "1"
    source_path: str | None = None
    flagged_injection: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoredChunk:
    id: str
    document_id: str
    ordinal: int
    heading: str
    content: str
    embedding: list[float]


@dataclass
class ChunkHit:
    chunk_id: str
    document_id: str
    title: str
    document_type: str
    service: str | None
    heading: str
    content: str
    score: float
    flagged_injection: bool


class KnowledgeStore(Protocol):
    def upsert(self, document: KnowledgeDocument, chunks: list[StoredChunk]) -> None: ...
    def has_hash(self, content_hash: str) -> bool: ...
    def vector_search(
        self, embedding: list[float], *, document_types: list[str] | None, service: str | None, k: int
    ) -> list[ChunkHit]: ...
    def keyword_search(
        self, query: str, *, document_types: list[str] | None, service: str | None, k: int
    ) -> list[ChunkHit]: ...
    def list_documents(self) -> list[dict[str, Any]]: ...


def _query_terms(query: str) -> list[str]:
    return sorted({t for t in tokenize(query) if len(t) > 1})


class PgVectorStore:
    """Vector similarity via pgvector (HNSW, cosine) and keyword ranking via ``ts_rank_cd``."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def upsert(self, document: KnowledgeDocument, chunks: list[StoredChunk]) -> None:
        with self._sf() as s, s.begin():
            s.execute(delete(DocumentChunkRow).where(DocumentChunkRow.document_id == document.id))
            s.merge(
                KnowledgeDocumentRow(
                    id=document.id,
                    title=document.title,
                    document_type=document.document_type,
                    service=document.service,
                    environment=document.environment,
                    team=document.team,
                    severity=document.severity,
                    incident_ref=document.incident_ref,
                    version=document.version,
                    source_path=document.source_path,
                    content_hash=document.content_hash,
                    flagged_injection=document.flagged_injection,
                    doc_metadata=document.metadata,
                )
            )
            s.flush()
            s.add_all(
                DocumentChunkRow(
                    id=c.id,
                    document_id=c.document_id,
                    ordinal=c.ordinal,
                    heading=c.heading,
                    content=c.content,
                    embedding=c.embedding,
                    chunk_metadata={},
                )
                for c in chunks
            )

    def has_hash(self, content_hash: str) -> bool:
        with self._sf() as s:
            return (
                s.scalar(select(KnowledgeDocumentRow.id).where(KnowledgeDocumentRow.content_hash == content_hash))
                is not None
            )

    @staticmethod
    def _filters(document_types: list[str] | None, service: str | None) -> tuple[str, dict[str, Any]]:
        clauses, params = [], {}
        if document_types:
            clauses.append("d.document_type = ANY(:types)")
            params["types"] = document_types
        if service:
            clauses.append("(d.service IS NULL OR d.service = :service)")
            params["service"] = service
        return (" AND " + " AND ".join(clauses)) if clauses else "", params

    def _hits(self, rows: Any) -> list[ChunkHit]:
        return [
            ChunkHit(
                chunk_id=r.id,
                document_id=r.document_id,
                title=r.title,
                document_type=r.document_type,
                service=r.service,
                heading=r.heading,
                content=r.content,
                score=float(r.score),
                flagged_injection=r.flagged_injection,
            )
            for r in rows
        ]

    def vector_search(
        self, embedding: list[float], *, document_types: list[str] | None, service: str | None, k: int
    ) -> list[ChunkHit]:
        where, params = self._filters(document_types, service)
        sql = text(
            "SELECT c.id, c.document_id, c.heading, c.content, d.title, d.document_type, d.service, "
            "d.flagged_injection, 1 - (c.embedding <=> CAST(:q AS vector)) AS score "
            "FROM document_chunks c JOIN knowledge_documents d ON d.id = c.document_id "
            f"WHERE TRUE{where} ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :k"
        )
        with self._sf() as s:
            rows = s.execute(sql, {"q": str(embedding), "k": k, **params}).all()
        return self._hits(rows)

    def keyword_search(
        self, query: str, *, document_types: list[str] | None, service: str | None, k: int
    ) -> list[ChunkHit]:
        terms = [re.sub(r"[^a-z0-9]", "", t) for t in _query_terms(query)]
        terms = [t for t in terms if t]
        if not terms:
            return []
        where, params = self._filters(document_types, service)
        sql = text(
            "SELECT c.id, c.document_id, c.heading, c.content, d.title, d.document_type, d.service, "
            "d.flagged_injection, ts_rank_cd(c.tsv, to_tsquery('english', :tsq)) AS score "
            "FROM document_chunks c JOIN knowledge_documents d ON d.id = c.document_id "
            f"WHERE c.tsv @@ to_tsquery('english', :tsq){where} ORDER BY score DESC LIMIT :k"
        )
        with self._sf() as s:
            rows = s.execute(sql, {"tsq": " | ".join(terms), "k": k, **params}).all()
        return self._hits(rows)

    def list_documents(self) -> list[dict[str, Any]]:
        sql = text(
            "SELECT d.id, d.title, d.document_type, d.service, d.version, d.flagged_injection, d.created_at, "
            "d.incident_ref, count(c.id) AS chunks FROM knowledge_documents d "
            "LEFT JOIN document_chunks c ON c.document_id = d.id GROUP BY d.id ORDER BY d.document_type, d.title"
        )
        with self._sf() as s:
            return [dict(r._mapping) for r in s.execute(sql).all()]


class InMemoryStore:
    """Numpy cosine similarity + Okapi BM25. Behaviourally equivalent for tests and offline evals."""

    def __init__(self) -> None:
        self._docs: dict[str, KnowledgeDocument] = {}
        self._chunks: dict[str, StoredChunk] = {}
        self._lock = threading.Lock()

    def upsert(self, document: KnowledgeDocument, chunks: list[StoredChunk]) -> None:
        with self._lock:
            for cid in [c for c, ch in self._chunks.items() if ch.document_id == document.id]:
                del self._chunks[cid]
            self._docs[document.id] = document
            for c in chunks:
                self._chunks[c.id] = c

    def has_hash(self, content_hash: str) -> bool:
        return any(d.content_hash == content_hash for d in self._docs.values())

    def _eligible(self, document_types: list[str] | None, service: str | None) -> list[StoredChunk]:
        out = []
        for c in self._chunks.values():
            d = self._docs[c.document_id]
            if document_types and d.document_type not in document_types:
                continue
            if service and d.service not in (None, service):
                continue
            out.append(c)
        return out

    def _hit(self, c: StoredChunk, score: float) -> ChunkHit:
        d = self._docs[c.document_id]
        return ChunkHit(
            chunk_id=c.id,
            document_id=d.id,
            title=d.title,
            document_type=d.document_type,
            service=d.service,
            heading=c.heading,
            content=c.content,
            score=score,
            flagged_injection=d.flagged_injection,
        )

    def vector_search(
        self, embedding: list[float], *, document_types: list[str] | None, service: str | None, k: int
    ) -> list[ChunkHit]:
        chunks = self._eligible(document_types, service)
        if not chunks:
            return []
        matrix = np.array([c.embedding for c in chunks], dtype=np.float32)
        scores = matrix @ np.array(embedding, dtype=np.float32)
        order = np.argsort(-scores)[:k]
        return [self._hit(chunks[i], float(scores[i])) for i in order]

    def keyword_search(
        self, query: str, *, document_types: list[str] | None, service: str | None, k: int
    ) -> list[ChunkHit]:
        terms = _query_terms(query)
        chunks = self._eligible(document_types, service)
        if not terms or not chunks:
            return []
        docs = [Counter(tokenize(f"{c.heading} {c.content}")) for c in chunks]
        avg_len = sum(sum(d.values()) for d in docs) / len(docs)
        n = len(docs)
        k1, b = 1.5, 0.75
        scores = []
        for doc in docs:
            length = sum(doc.values())
            score = 0.0
            for t in terms:
                df = sum(1 for d in docs if t in d)
                if not df or t not in doc:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                tf = doc[t]
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg_len))
            scores.append(score)
        ranked = sorted(((s, c) for s, c in zip(scores, chunks, strict=True) if s > 0), key=lambda x: -x[0])[:k]
        return [self._hit(c, s) for s, c in ranked]

    def list_documents(self) -> list[dict[str, Any]]:
        return [
            {
                "id": d.id,
                "title": d.title,
                "document_type": d.document_type,
                "service": d.service,
                "version": d.version,
                "flagged_injection": d.flagged_injection,
                "incident_ref": d.incident_ref,
                "chunks": sum(1 for c in self._chunks.values() if c.document_id == d.id),
            }
            for d in sorted(self._docs.values(), key=lambda d: (d.document_type, d.title))
        ]
