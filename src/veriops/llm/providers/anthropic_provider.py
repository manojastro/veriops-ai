"""Anthropic provider. Structured output is obtained by forcing a single tool call whose input
schema is the requested Pydantic schema."""

from __future__ import annotations

from typing import Any

from veriops.llm.providers.base import ProviderRequest, ProviderResponse
from veriops.shared.config import Settings
from veriops.shared.errors import LLMError


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError("install the 'llm' extra to use the Anthropic provider") from exc
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is required")
        self._anthropic = anthropic
        self._client: Any = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        user = request.user_prompt
        if request.repair_hint:
            user += f"\n\nYour previous output was invalid: {request.repair_hint}"
        try:
            message = self._client.messages.create(
                model=request.model,
                max_tokens=4096,
                system=request.system_prompt,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {
                        "name": "emit_result",
                        "description": "Return the structured result.",
                        "input_schema": request.schema.model_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": "emit_result"},
                timeout=request.timeout_s,
            )
        except self._anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}", retryable=True) from exc
        except (self._anthropic.APITimeoutError, self._anthropic.APIConnectionError) as exc:
            raise LLMError(f"transport: {exc}", retryable=True) from exc
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"api error {exc.status_code}", retryable=exc.status_code >= 500) from exc
        block = next((b for b in message.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            raise LLMError("model did not return a tool call", retryable=True)
        return ProviderResponse(
            raw=dict(block.input),
            prompt_tokens=message.usage.input_tokens,
            completion_tokens=message.usage.output_tokens,
        )
