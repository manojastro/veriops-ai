"""Idempotent seed data: service catalog, local demo users and the knowledge corpus."""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from veriops.db.models import KnowledgeDocumentRow, ServiceRow, UserRow
from veriops.db.session import session_scope
from veriops.rag.ingest import DocumentIngestor
from veriops.security.auth import hash_password
from veriops.shared.config import Settings
from veriops.simulator.catalog import SERVICES

logger = logging.getLogger(__name__)

DEMO_USERS = [
    ("usr-admin", "admin", "Ada Admin", "administrator"),
    ("usr-alice", "alice", "Alice Approver", "approver"),
    ("usr-bob", "bob", "Bob Engineer", "engineer"),
    ("usr-victor", "victor", "Victor Viewer", "viewer"),
]


def seed_services() -> None:
    with session_scope() as s:
        for p in SERVICES.values():
            s.merge(
                ServiceRow(
                    name=p.name,
                    team=p.team,
                    criticality=p.criticality,
                    tier=0 if p.criticality == "critical" else 1,
                    description=p.description,
                    slo_p95_latency_ms=p.slo_p95_latency_ms,
                    slo_error_rate_pct=p.slo_error_rate_pct,
                )
            )


def seed_demo_users(settings: Settings) -> None:
    if not settings.seed_demo_users:
        return
    password_hash = hash_password(settings.demo_user_password.get_secret_value())
    with session_scope() as s:
        for user_id, username, name, role in DEMO_USERS:
            if s.scalar(select(UserRow.id).where(UserRow.username == username)) is None:
                s.add(UserRow(id=user_id, username=username, display_name=name, password_hash=password_hash, role=role))


def seed_knowledge(ingestor: DocumentIngestor) -> int:
    with session_scope() as s:
        count = s.scalar(select(func.count()).select_from(KnowledgeDocumentRow)) or 0
    if count:
        return 0
    results = ingestor.ingest_directory()
    logger.info("ingested %d knowledge documents", len(results))
    return len(results)
