"""OpenAI, Azure OpenAI (Microsoft Foundry) and OpenAI-compatible providers.

Uses JSON-schema structured outputs. The response is still validated with Pydantic by the gateway;
``strict`` mode is not used because it forbids optional fields.
"""

from __future__ import annotations

import json
from typing import Any

from veriops.llm.providers.base import ProviderRequest, ProviderResponse, estimate_tokens
from veriops.shared.config import LLMProvider, Settings
from veriops.shared.errors import LLMError


class OpenAIChatProvider:
    def __init__(self, settings: Settings) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise LLMError("install the 'llm' extra to use OpenAI providers") from exc
        self._openai = openai
        provider = settings.llm_provider
        self.name = provider.value
        if provider == LLMProvider.AZURE_OPENAI:
            if not (settings.azure_openai_endpoint and settings.azure_openai_api_key):
                raise LLMError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are required")
            self._client: Any = openai.AzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key.get_secret_value(),
                api_version=settings.azure_openai_api_version,
                max_retries=0,
            )
        else:
            key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
            if provider == LLMProvider.OPENAI and not key:
                raise LLMError("OPENAI_API_KEY is required")
            self._client = openai.OpenAI(api_key=key or "not-needed", base_url=settings.openai_base_url, max_retries=0)

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        messages = [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ]
        if request.repair_hint:
            messages.append(
                {
                    "role": "user",
                    "content": f"Your previous output was invalid: {request.repair_hint}. Return corrected JSON only.",
                }
            )
        try:
            response = self._client.chat.completions.create(
                model=request.model,
                messages=messages,
                temperature=0,
                timeout=request.timeout_s,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": request.schema.__name__, "schema": request.schema.model_json_schema()},
                },
            )
        except self._openai.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}", retryable=True) from exc
        except (self._openai.APITimeoutError, self._openai.APIConnectionError) as exc:
            raise LLMError(f"transport: {exc}", retryable=True) from exc
        except self._openai.APIStatusError as exc:
            raise LLMError(f"api error {exc.status_code}", retryable=exc.status_code >= 500) from exc
        content = response.choices[0].message.content or "{}"
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"non-JSON output: {exc}", retryable=True) from exc
        usage = response.usage
        return ProviderResponse(
            raw=raw,
            prompt_tokens=usage.prompt_tokens if usage else estimate_tokens(request.user_prompt),
            completion_tokens=usage.completion_tokens if usage else estimate_tokens(content),
        )
