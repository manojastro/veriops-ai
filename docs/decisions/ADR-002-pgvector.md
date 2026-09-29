# ADR-002: PostgreSQL + pgvector as the vector store

**Status:** Accepted

## Context
The system already needs PostgreSQL for incidents, audit and checkpoints. Retrieval needs vector similarity,
keyword ranking and metadata filters (service, document type).

## Decision
Store chunks in `document_chunks` with a `vector(384)` column (HNSW, cosine) and a generated `tsvector` column
(GIN). Both searches run in SQL with the same metadata filters.

## Consequences
+ One database to operate, back up and secure; transactional ingestion; joins with document metadata.
+ Azure PostgreSQL Flexible Server supports the `vector` extension, so production matches local.
− Not a specialised vector DB; fine for the operational-knowledge scale (thousands to low millions of chunks).
