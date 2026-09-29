"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from veriops.shared.config import get_settings


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    database_url = url or get_settings().database_url
    return create_engine(database_url, pool_pre_ping=True, pool_size=10, max_overflow=10, future=True)


@lru_cache(maxsize=4)
def _session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = _session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database(url: str | None = None) -> bool:
    try:
        with get_engine(url).connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def psycopg_conninfo(url: str | None = None) -> str:
    """Convert a SQLAlchemy URL into a libpq conninfo string (used by the LangGraph checkpointer)."""
    database_url = url or get_settings().database_url
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
