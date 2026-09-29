"""Knowledge base routes (RAG)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from veriops.api.deps import require, runtime_dep
from veriops.api.runtime import Runtime
from veriops.api.schemas import KnowledgeDocumentRequest
from veriops.domain.enums import ActorType
from veriops.domain.models import AuditRecord
from veriops.security.auth import Principal

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/documents", status_code=201, summary="Ingest a document (chunk, embed, index)")
def ingest(
    body: KnowledgeDocumentRequest,
    rt: Runtime = Depends(runtime_dep),
    principal: Principal = Depends(require("knowledge:write")),
) -> dict[str, Any]:
    meta = body.model_dump(exclude={"content"}, exclude_none=True)
    result = rt.platform.ingestor.ingest_text(body.content, meta, source_path=f"api:{principal.username}")
    rt.platform.retriever.invalidate()
    rt.recorder.audit(
        AuditRecord(
            incident_id=None,
            actor=principal.username,
            actor_type=ActorType.HUMAN,
            action="knowledge.ingest",
            input={"title": body.title, "type": body.document_type},
            output={
                "document_id": result.document_id,
                "chunks": result.chunks,
                "flagged_injection": result.flagged_injection,
            },
            decision="FLAGGED" if result.flagged_injection else "INGESTED",
        )
    )
    return result.__dict__


@router.get("/documents", summary="List knowledge documents")
def documents(
    rt: Runtime = Depends(runtime_dep), _: Principal = Depends(require("knowledge:read"))
) -> list[dict[str, Any]]:
    return rt.platform.store.list_documents()


@router.get("/search", summary="Hybrid search (vector + full text + RRF + rerank) with scores")
def search(
    q: str = Query(min_length=3, max_length=500),
    service: str | None = Query(default=None, max_length=64),
    document_type: list[str] | None = Query(default=None),
    top_k: int = Query(default=5, ge=1, le=20),
    rt: Runtime = Depends(runtime_dep),
    _: Principal = Depends(require("knowledge:read")),
) -> list[dict[str, Any]]:
    citations = rt.platform.retriever.search(q, document_types=document_type, service=service, top_k=top_k)
    return [c.model_dump() for c in citations]
