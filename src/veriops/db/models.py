"""SQLAlchemy ORM models.

Design notes
------------
* Rich, evolving payloads (tool outputs, validation checks) are stored as JSONB next to the
  columns we filter or aggregate on.
* ``audit_events`` is append-only: a database trigger (see the initial migration) rejects UPDATE
  and DELETE statements, so even a compromised application role cannot rewrite history.
* ``document_chunks`` carries both a pgvector embedding (HNSW index) and a generated ``tsvector``
  (GIN index) so hybrid retrieval runs in the database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from veriops.shared.clock import utcnow

EMBEDDING_DIMENSIONS = 384


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


# --- identity -------------------------------------------------------------------------------


class RoleRow(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[str] = mapped_column(String(255))
    permissions: Mapped[list[Any]] = mapped_column(JSONB, default=list)


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(ForeignKey("roles.name"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts()


# --- catalog --------------------------------------------------------------------------------


class ServiceRow(Base):
    __tablename__ = "services"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    team: Mapped[str] = mapped_column(String(64))
    criticality: Mapped[str] = mapped_column(String(16))  # critical | high | medium | low
    tier: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="")
    slo_p95_latency_ms: Mapped[float] = mapped_column(Float, default=500)
    slo_error_rate_pct: Mapped[float] = mapped_column(Float, default=1.0)


# --- incidents ------------------------------------------------------------------------------


class IncidentRow(Base):
    __tablename__ = "incidents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    service: Mapped[str] = mapped_column(String(64), index=True)
    environment: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(32), index=True)
    scenario: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alert: Mapped[dict[str, Any]] = mapped_column(JSONB)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    events: Mapped[list[IncidentEventRow]] = relationship(back_populates="incident", order_by="IncidentEventRow.seq")


class IncidentEventRow(Base):
    """Timeline events. ``seq`` provides a total order used by the SSE stream."""

    __tablename__ = "incident_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = _ts()
    type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="info")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    incident: Mapped[IncidentRow] = relationship(back_populates="events")


class EvidenceRow(Base):
    __tablename__ = "evidence_items"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    source_tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    collected_at: Mapped[datetime] = _ts()


class MetricsSnapshotRow(Base):
    __tablename__ = "metrics_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    phase: Mapped[str] = mapped_column(String(32))  # alert | investigation | pre_remediation | post_remediation
    service: Mapped[str] = mapped_column(String(64))
    p95_latency_ms: Mapped[float] = mapped_column(Float)
    error_rate_pct: Mapped[float] = mapped_column(Float)
    cpu_pct: Mapped[float] = mapped_column(Float)
    memory_pct: Mapped[float] = mapped_column(Float)
    db_pool_utilization_pct: Mapped[float] = mapped_column(Float)
    rps: Mapped[float] = mapped_column(Float, default=0)
    series: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    captured_at: Mapped[datetime] = _ts()


class LogEvidenceRow(Base):
    __tablename__ = "log_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(40))
    query: Mapped[str] = mapped_column(String(255))
    total: Mapped[int] = mapped_column(Integer)
    counts_by_level: Mapped[dict[str, Any]] = mapped_column(JSONB)
    top_patterns: Mapped[list[Any]] = mapped_column(JSONB)
    samples: Mapped[list[Any]] = mapped_column(JSONB)
    captured_at: Mapped[datetime] = _ts()


class TraceEvidenceRow(Base):
    __tablename__ = "trace_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(String(40))
    trace_id: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[float] = mapped_column(Float)
    slowest_span: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spans: Mapped[list[Any]] = mapped_column(JSONB)
    captured_at: Mapped[datetime] = _ts()


class HypothesisRow(Base):
    __tablename__ = "hypotheses"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    probability: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class HypothesisTestRow(Base):
    __tablename__ = "hypothesis_tests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    hypothesis_id: Mapped[str] = mapped_column(String(40), index=True)
    verdict: Mapped[str] = mapped_column(String(32))
    calibrated_confidence: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    tested_at: Mapped[datetime] = _ts()


class RemediationPlanRow(Base):
    __tablename__ = "remediation_plans"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    hypothesis_id: Mapped[str] = mapped_column(String(40))
    action_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts()


class ValidationRunRow(Base):
    __tablename__ = "validation_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(40), index=True)
    validator: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validated_at: Mapped[datetime] = _ts()


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32))
    requested_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ExecutionRow(Base):
    __tablename__ = "executions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(32))  # remediation | verification | rollback
    success: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    executed_at: Mapped[datetime] = _ts()


class AuditEventRow(Base):
    """Append-only audit log (UPDATE/DELETE blocked by trigger)."""

    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True)
    ts: Mapped[datetime] = _ts()
    incident_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(64))
    actor_type: Mapped[str] = mapped_column(String(16))
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(40), index=True)
    node: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    duration_ms: Mapped[float] = mapped_column(Float)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = _ts()


class LLMCallRow(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    agent: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()


class ToolCallRow(Base):
    __tablename__ = "tool_calls"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    tool: Mapped[str] = mapped_column(String(64), index=True)
    permission: Mapped[str] = mapped_column(String(32))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[float] = mapped_column(Float)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()


# --- knowledge ------------------------------------------------------------------------------


class KnowledgeDocumentRow(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    service: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    environment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    team: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(8), nullable=True)
    incident_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    version: Mapped[str] = mapped_column(String(16), default="1")
    source_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    flagged_injection: Mapped[bool] = mapped_column(Boolean, default=False)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = _ts()


class DocumentChunkRow(Base):
    __tablename__ = "document_chunks"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(heading, '') || ' ' || content)", persisted=True)
    )
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# --- evaluation -----------------------------------------------------------------------------


class EvaluationRunRow(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    scenario_count: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    results: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    passed_thresholds: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
