"""Provider protocol shared by all LLM backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from veriops.llm.schemas import LLMTask


@dataclass(frozen=True)
class ProviderRequest:
    task: LLMTask
    model: str
    system_prompt: str
    user_prompt: str
    context: dict[str, Any]
    schema: type[BaseModel]
    timeout_s: float
    repair_hint: str | None = None  # validation error from a previous attempt


@dataclass(frozen=True)
class ProviderResponse:
    raw: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int


class LLMProviderClient(Protocol):
    name: str

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) used when a provider does not report usage."""
    return max(1, len(text) // 4)
