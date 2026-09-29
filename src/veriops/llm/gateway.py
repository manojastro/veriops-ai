"""LLM gateway: the only way the platform talks to a model.

Responsibilities: routing (task → model tier), prompt rendering with data/instruction separation,
bounded retries for transient errors, schema validation with one repair attempt, response caching
(only for identical context), cost/token accounting, tracing and observer notification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from veriops.llm.pricing import estimate_cost
from veriops.llm.prompts import SYSTEM_PROMPTS, render_user_prompt
from veriops.llm.providers.base import LLMProviderClient, ProviderRequest
from veriops.llm.router import ModelRouter
from veriops.llm.schemas import TASK_SCHEMAS, LLMTask
from veriops.observability import metrics as obs_metrics
from veriops.observability.tracing import span
from veriops.shared.config import LLMProvider, Settings
from veriops.shared.errors import LLMError, LLMOutputValidationError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMCallRecord:
    incident_id: str | None
    agent: str
    task: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float
    cached: bool
    success: bool
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMObserver(Protocol):
    def on_llm_call(self, record: LLMCallRecord) -> None: ...


@dataclass
class LLMGateway:
    provider: LLMProviderClient
    router: ModelRouter
    max_retries: int = 2
    timeout_s: float = 45.0
    cache_enabled: bool = True
    cache_size: int = 256
    observers: list[LLMObserver] = field(default_factory=list)
    _cache: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    ledger: list[LLMCallRecord] = field(default_factory=list)

    def structured(
        self,
        task: LLMTask,
        context: dict[str, Any],
        *,
        agent: str,
        incident_id: str | None = None,
        schema: type[T] | None = None,
    ) -> T:
        schema_cls: type[BaseModel] = schema or TASK_SCHEMAS[task]
        route = self.router.route(task)
        system_prompt = SYSTEM_PROMPTS[task]
        user_prompt = render_user_prompt(task, context)
        cache_key = hashlib.sha256(f"{route.model}|{task}|{user_prompt}".encode()).hexdigest()

        with span(
            "llm.call",
            {
                "llm.task": task.value,
                "llm.model": route.model,
                "llm.provider": self.provider.name,
                "veriops.agent": agent,
            },
        ):
            if self.cache_enabled:
                with self._lock:
                    cached = self._cache.get(cache_key)
                if cached is not None:
                    self._record(
                        LLMCallRecord(
                            incident_id,
                            agent,
                            task.value,
                            self.provider.name,
                            route.model,
                            0,
                            0,
                            0.0,
                            0.0,
                            cached=True,
                            success=True,
                        )
                    )
                    return schema_cls.model_validate(cached)  # type: ignore[return-value]

            repair_hint: str | None = None
            last_error: Exception | None = None
            for attempt in range(1, self.max_retries + 2):
                started = time.perf_counter()
                request = ProviderRequest(
                    task=task,
                    model=route.model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    context=context,
                    schema=schema_cls,
                    timeout_s=self.timeout_s,
                    repair_hint=repair_hint,
                )
                try:
                    response = self.provider.complete(request)
                    parsed = schema_cls.model_validate(response.raw)
                except ValidationError as exc:
                    last_error = LLMOutputValidationError(f"schema validation failed: {exc.error_count()} errors")
                    repair_hint = str(exc)[:800]
                    self._record(
                        LLMCallRecord(
                            incident_id,
                            agent,
                            task.value,
                            self.provider.name,
                            route.model,
                            0,
                            0,
                            0.0,
                            (time.perf_counter() - started) * 1000,
                            False,
                            False,
                            str(last_error),
                        )
                    )
                    continue
                except LLMError as exc:
                    last_error = exc
                    self._record(
                        LLMCallRecord(
                            incident_id,
                            agent,
                            task.value,
                            self.provider.name,
                            route.model,
                            0,
                            0,
                            0.0,
                            (time.perf_counter() - started) * 1000,
                            False,
                            False,
                            str(exc),
                        )
                    )
                    if not exc.retryable:
                        break
                    time.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)))
                    continue
                latency = (time.perf_counter() - started) * 1000
                cost = estimate_cost(route.model, response.prompt_tokens, response.completion_tokens)
                self._record(
                    LLMCallRecord(
                        incident_id,
                        agent,
                        task.value,
                        self.provider.name,
                        route.model,
                        response.prompt_tokens,
                        response.completion_tokens,
                        cost,
                        latency,
                        cached=False,
                        success=True,
                    )
                )
                if self.cache_enabled:
                    with self._lock:
                        self._cache[cache_key] = parsed.model_dump(mode="json")
                        while len(self._cache) > self.cache_size:
                            self._cache.popitem(last=False)
                return parsed  # type: ignore[return-value]
        raise LLMError(f"{task.value} failed after retries: {last_error}")

    def _record(self, record: LLMCallRecord) -> None:
        self.ledger.append(record)
        obs_metrics.record_llm_call(
            record.provider,
            record.model,
            record.task,
            record.prompt_tokens,
            record.completion_tokens,
            record.cost_usd,
            record.success,
            record.cached,
        )
        for observer in self.observers:
            try:
                observer.on_llm_call(record)
            except Exception:
                logger.exception("llm observer failed")


def build_provider(settings: Settings) -> LLMProviderClient:
    match settings.llm_provider:
        case LLMProvider.SIMULATED:
            from veriops.llm.providers.simulated import SimulatedProvider

            return SimulatedProvider()
        case LLMProvider.ANTHROPIC:
            from veriops.llm.providers.anthropic_provider import AnthropicProvider

            return AnthropicProvider(settings)
        case _:
            from veriops.llm.providers.openai_provider import OpenAIChatProvider

            return OpenAIChatProvider(settings)


def build_gateway(settings: Settings, provider: LLMProviderClient | None = None) -> LLMGateway:
    return LLMGateway(
        provider=provider or build_provider(settings),
        router=ModelRouter(settings),
        max_retries=settings.max_llm_retries,
        timeout_s=settings.llm_timeout_seconds,
        cache_enabled=settings.llm_cache_enabled,
    )


def context_digest(context: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest()[:16]
