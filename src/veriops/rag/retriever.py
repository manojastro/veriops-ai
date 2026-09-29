"""Hybrid retrieval: vector similarity + keyword ranking → Reciprocal Rank Fusion → reranking.

Every returned citation carries all four scores so the UI and audit log can show *why* a document
was retrieved. Document text is sanitised (prompt-injection defence) before it leaves this module.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field

from veriops.domain.models import DocumentCitation
from veriops.observability import metrics as obs_metrics
from veriops.observability.tracing import span
from veriops.rag.embeddings import Embedder, tokenize
from veriops.rag.store import ChunkHit, KnowledgeStore
from veriops.security.sanitizer import sanitize

RRF_K = 60


def reciprocal_rank_fusion(*rankings: list[ChunkHit], k: int = RRF_K) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
    return fused


class HeuristicReranker:
    """Deterministic cross-feature reranker.

    score = 0.45·fused(normalised) + 0.35·query-term coverage (heading-weighted) + 0.20·metadata match.
    A cross-encoder or LLM reranker can replace this behind the same interface.
    """

    def score(self, query: str, hit: ChunkHit, fused_norm: float, service: str | None) -> float:
        terms = set(tokenize(query))
        body = set(tokenize(hit.content))
        heading = set(tokenize(f"{hit.title} {hit.heading}"))
        coverage = 0.0
        if terms:
            coverage = min(1.0, (len(terms & body) + 1.5 * len(terms & heading)) / len(terms))
        meta = 0.5 if service is None else 1.0 if hit.service == service else (0.5 if hit.service is None else 0.0)
        return round(0.45 * fused_norm + 0.35 * coverage + 0.20 * meta, 4)


@dataclass
class HybridRetriever:
    store: KnowledgeStore
    embedder: Embedder
    reranker: HeuristicReranker = field(default_factory=HeuristicReranker)
    candidate_k: int = 20
    cache_ttl_s: float = 60.0
    sanitize_documents: bool = True
    _cache: dict[str, tuple[float, list[DocumentCitation]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def search(
        self, query: str, *, document_types: list[str] | None = None, service: str | None = None, top_k: int = 5
    ) -> list[DocumentCitation]:
        key = hashlib.sha256(f"{query}|{sorted(document_types or [])}|{service}|{top_k}".encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
        started = time.perf_counter()
        with span("rag.search", {"rag.query": query[:200], "rag.service": service}):
            vector_hits = self.store.vector_search(
                self.embedder.embed([query])[0], document_types=document_types, service=service, k=self.candidate_k
            )
            keyword_hits = self.store.keyword_search(
                query, document_types=document_types, service=service, k=self.candidate_k
            )
            fused = reciprocal_rank_fusion(vector_hits, keyword_hits)
            by_id = {h.chunk_id: h for h in vector_hits + keyword_hits}
            vec_scores = {h.chunk_id: h.score for h in vector_hits}
            kw_scores = {h.chunk_id: h.score for h in keyword_hits}
            max_fused = max(fused.values(), default=1.0)
            scored = []
            for chunk_id, fused_score in fused.items():
                hit = by_id[chunk_id]
                rerank = self.reranker.score(query, hit, fused_score / max_fused, service)
                scored.append((rerank, fused_score, hit))
            scored.sort(key=lambda x: (-x[0], -x[1]))

            citations: list[DocumentCitation] = []
            seen_docs: set[str] = set()
            for rerank, fused_score, hit in scored:
                if hit.document_id in seen_docs:  # one chunk per document keeps results diverse
                    continue
                seen_docs.add(hit.document_id)
                content = hit.content
                flagged = hit.flagged_injection
                if self.sanitize_documents:
                    cleaned = sanitize(content)
                    content, flagged = cleaned.text, flagged or cleaned.flagged
                citations.append(
                    DocumentCitation(
                        document_id=hit.document_id,
                        chunk_id=hit.chunk_id,
                        title=hit.title,
                        document_type=hit.document_type,
                        snippet=content[:700],
                        vector_score=round(vec_scores.get(chunk_id := hit.chunk_id, 0.0), 4),
                        keyword_score=round(kw_scores.get(chunk_id, 0.0), 4),
                        fused_score=round(fused_score, 5),
                        rerank_score=rerank,
                        flagged_injection=flagged,
                    )
                )
                if len(citations) >= top_k:
                    break
        obs_metrics.RETRIEVALS.labels(type(self.store).__name__).observe(time.perf_counter() - started)
        with self._lock:
            self._cache[key] = (time.monotonic() + self.cache_ttl_s, citations)
        return citations
