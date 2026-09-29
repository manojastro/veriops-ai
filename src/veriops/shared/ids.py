"""Identifier helpers. IDs are prefixed so they are self-describing in logs and audit trails."""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
