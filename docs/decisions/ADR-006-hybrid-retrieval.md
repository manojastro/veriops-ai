# ADR-006: Hybrid retrieval with RRF and reranking

**Status:** Accepted

## Context
Operational queries mix exact identifiers (`payment.gateway.provider`, `INC-2025-114`, `QueuePool`) with
semantic intent ("database slow after deploy"). Vector-only search misses identifiers; keyword-only misses
paraphrase.

## Decision
Run pgvector cosine search and PostgreSQL full-text ranking in parallel (top 20 each), fuse with Reciprocal Rank
Fusion (k = 60), then rerank with a deterministic cross-feature scorer (fused score, heading-weighted query
coverage, service metadata match). Return one chunk per document with all four scores and a citation id.
Sanitise snippets for prompt injection before they leave the retriever.

## Consequences
+ Robust results even with the offline hashing embedder; scores are explainable in the UI.
+ The reranker is swappable (cross-encoder or LLM) behind the same interface.
− Two queries per search (cheap in PostgreSQL; results cached for 60 s).
