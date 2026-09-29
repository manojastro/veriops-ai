"""Time source. Centralised so tests and the simulator can reason about time consistently."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
