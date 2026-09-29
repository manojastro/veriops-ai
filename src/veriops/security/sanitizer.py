"""Prompt-injection defence for untrusted text (retrieved documents, logs, tool output).

Retrieved documents are data, not instructions. Before any document text reaches an LLM prompt,
lines that look like instructions aimed at the model or at the executor are replaced with a marker
and the document is flagged. This is one layer of several (see docs/security/THREAT_MODEL.md):
data/instruction separation in prompts, schema-constrained outputs with an enum of actions, the
tool allow-list, deterministic validation and the policy engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REDACTION_MARKER = "[removed: instruction-like content in untrusted document]"

INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "override_instructions",
        re.compile(r"(?i)\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions|rules|prompt|guidelines)\b"),
    ),
    ("role_hijack", re.compile(r"(?i)\byou are now\b|\bact as (an? )?(admin|root|system)\b|\bnew system prompt\b")),
    ("execute_directive", re.compile(r"(?i)\b(execute|run)\s*(this|the following)?\s*(command|statement|sql)?\s*:")),
    ("destructive_sql", re.compile(r"(?i)\b(drop\s+(table|database|schema)|truncate\s+table|delete\s+from)\b")),
    (
        "credential_exfiltration",
        re.compile(
            r"(?i)\b(send|post|email|upload|exfiltrate|share)\b.{0,60}"
            r"\b(credential|password|secret|api[ _-]?key|token)s?\b"
        ),
    ),
    ("remote_code", re.compile(r"(?i)(curl|wget)\s+\S+\s*\|\s*(ba)?sh")),
    (
        "disable_controls",
        re.compile(r"(?i)\b(disable|turn off|bypass)\b.{0,30}\b(security|approval|validation|audit|guardrail)s?\b"),
    ),
]


@dataclass
class SanitizedText:
    text: str
    flagged: bool
    findings: list[str] = field(default_factory=list)


def scan(text: str) -> list[str]:
    return sorted({name for name, pattern in INJECTION_PATTERNS if pattern.search(text)})


def sanitize(text: str) -> SanitizedText:
    findings: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        hits = [name for name, pattern in INJECTION_PATTERNS if pattern.search(line)]
        if hits:
            findings.update(hits)
            lines.append(REDACTION_MARKER)
        else:
            lines.append(line)
    return SanitizedText(text="\n".join(lines), flagged=bool(findings), findings=sorted(findings))
