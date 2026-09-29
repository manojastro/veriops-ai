"""Redaction of sensitive values before they are persisted to audit logs or sent to an LLM."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

_SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|credential|private[_-]?key|conn(ection)?_?string)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(postgres(ql)?|mysql|redis|mongodb)(\+\w+)?://[^:\s]+:[^@\s]+@"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact_text(value: str) -> str:
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact(value: Any) -> Any:
    """Recursively redact sensitive keys and value patterns in JSON-like data."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEYS.search(k) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
