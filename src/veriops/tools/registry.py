"""MCP-style tool registry with permission classes, validation, retries, timeouts and caching.

Every tool call — by an agent, a graph node, or the evaluation harness — goes through
``ToolRegistry.invoke``. That single choke point enforces:

1. **Allow-list**: unknown tools are rejected.
2. **Permission class**: ``READ_ONLY`` always allowed; ``LOW_RISK_WRITE`` only when the caller's
   context grants it; ``HIGH_RISK_WRITE`` only with a recorded human approval; ``BLOCKED`` never.
3. **Input validation** against the tool's Pydantic input model.
4. **Bounded retries** with exponential backoff for retryable failures, and a per-call timeout.
5. **Caching** only for tools explicitly marked cacheable (immutable data such as Git commits, or
   short-TTL reads); any write invalidates cached entries for the affected service.
6. **Observation**: every call is reported to registered observers (DB recorder, metrics, tracing).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from veriops.domain.enums import ToolPermission
from veriops.shared.errors import ToolError, ToolPermissionError, ToolTimeoutError
from veriops.shared.ids import new_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    permission: ToolPermission
    input_model: type[BaseModel]
    handler: Callable[[Any], Any]
    cache_ttl_s: float = 0.0  # 0 = never cache
    timeout_s: float | None = None

    def descriptor(self) -> dict[str, Any]:
        """MCP ``tools/list`` style descriptor."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_model.model_json_schema(),
            "annotations": {
                "permission": self.permission.value,
                "readOnlyHint": self.permission == ToolPermission.READ_ONLY,
                "destructiveHint": self.permission in (ToolPermission.HIGH_RISK_WRITE, ToolPermission.BLOCKED),
            },
        }


@dataclass
class ToolContext:
    incident_id: str | None
    actor: str
    granted: frozenset[ToolPermission] = frozenset({ToolPermission.READ_ONLY})
    approval_id: str | None = None  # set only after a recorded human approval


class ToolResult(BaseModel):
    call_id: str
    tool: str
    permission: ToolPermission
    ok: bool
    data: Any = None
    error: str | None = None
    attempts: int = 1
    latency_ms: float = 0.0
    cached: bool = False
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolObserver(Protocol):
    def on_tool_call(self, result: ToolResult, ctx: ToolContext) -> None: ...


@dataclass
class _CacheEntry:
    expires_at: float
    data: Any


@dataclass
class ToolRegistry:
    max_retries: int = 3
    default_timeout_s: float = 10.0
    backoff_base_s: float = 0.2
    _specs: dict[str, ToolSpec] = field(default_factory=dict)
    _observers: list[ToolObserver] = field(default_factory=list)
    _cache: dict[str, _CacheEntry] = field(default_factory=dict)
    _executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=16))

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name} already registered")
        self._specs[spec.name] = spec

    def add_observer(self, observer: ToolObserver) -> None:
        self._observers.append(observer)

    def spec(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise ToolPermissionError(name, "tool is not on the allow-list")
        return self._specs[name]

    def descriptors(self) -> list[dict[str, Any]]:
        return [s.descriptor() for s in self._specs.values()]

    def names(self) -> list[str]:
        return list(self._specs)

    # --- authorisation ----------------------------------------------------------------------

    @staticmethod
    def authorize(spec: ToolSpec, ctx: ToolContext) -> None:
        if spec.permission == ToolPermission.BLOCKED:
            raise ToolPermissionError(spec.name, "tool is BLOCKED by policy and can never be executed")
        if spec.permission == ToolPermission.READ_ONLY:
            return
        if spec.permission not in ctx.granted:
            raise ToolPermissionError(spec.name, f"{spec.permission.value} not granted in this context")
        if spec.permission == ToolPermission.HIGH_RISK_WRITE and not ctx.approval_id:
            raise ToolPermissionError(spec.name, "HIGH_RISK_WRITE requires a recorded human approval")

    # --- invocation -------------------------------------------------------------------------

    def invoke(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        call_id = new_id("tc")
        started = time.perf_counter()
        spec = self._specs.get(name)
        if spec is None:
            result = ToolResult(
                call_id=call_id,
                tool=name,
                permission=ToolPermission.BLOCKED,
                ok=False,
                error="tool is not on the allow-list",
                arguments=arguments,
            )
            self._notify(result, ctx)
            return result
        try:
            self.authorize(spec, ctx)
            params = spec.input_model.model_validate(arguments)
        except ToolPermissionError as exc:
            result = ToolResult(
                call_id=call_id, tool=name, permission=spec.permission, ok=False, error=str(exc), arguments=arguments
            )
            self._notify(result, ctx)
            return result
        except ValidationError as exc:
            result = ToolResult(
                call_id=call_id,
                tool=name,
                permission=spec.permission,
                ok=False,
                error=f"invalid arguments: {exc.errors(include_url=False)}",
                arguments=arguments,
            )
            self._notify(result, ctx)
            return result

        cache_key = f"{name}:{json.dumps(params.model_dump(mode='json'), sort_keys=True)}"
        if spec.cache_ttl_s > 0:
            entry = self._cache.get(cache_key)
            if entry and entry.expires_at > time.monotonic():
                result = ToolResult(
                    call_id=call_id,
                    tool=name,
                    permission=spec.permission,
                    ok=True,
                    data=entry.data,
                    cached=True,
                    arguments=arguments,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                self._notify(result, ctx)
                return result

        attempts = 0
        last_error: str | None = None
        timeout = spec.timeout_s or self.default_timeout_s
        while attempts < max(1, self.max_retries):
            attempts += 1
            try:
                data = self._run_with_timeout(spec, params, timeout)
                if spec.permission != ToolPermission.READ_ONLY:
                    self._invalidate(arguments.get("service"))
                elif spec.cache_ttl_s > 0:
                    self._cache[cache_key] = _CacheEntry(time.monotonic() + spec.cache_ttl_s, data)
                result = ToolResult(
                    call_id=call_id,
                    tool=name,
                    permission=spec.permission,
                    ok=True,
                    data=data,
                    attempts=attempts,
                    arguments=arguments,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                self._notify(result, ctx)
                return result
            except ToolError as exc:
                last_error = str(exc)
                # Writes are never retried automatically: a timed-out write may have been applied.
                if not exc.retryable or spec.permission != ToolPermission.READ_ONLY:
                    break
            except Exception as exc:  # defensive: tool bugs must not crash the graph
                last_error = f"{type(exc).__name__}: {exc}"
                break
            time.sleep(self.backoff_base_s * (2 ** (attempts - 1)))

        result = ToolResult(
            call_id=call_id,
            tool=name,
            permission=spec.permission,
            ok=False,
            error=last_error,
            attempts=attempts,
            arguments=arguments,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        self._notify(result, ctx)
        return result

    def _run_with_timeout(self, spec: ToolSpec, params: BaseModel, timeout: float) -> Any:
        future = self._executor.submit(spec.handler, params)
        try:
            raw = future.result(timeout=timeout)
        except FutureTimeout as exc:
            raise ToolTimeoutError(spec.name, timeout) from exc
        return _to_jsonable(raw)

    def _invalidate(self, service: Any) -> None:
        if not service:
            self._cache.clear()
            return
        needle = f'"service": "{service}"'
        for key in [k for k in self._cache if needle in k]:
            self._cache.pop(key, None)

    def _notify(self, result: ToolResult, ctx: ToolContext) -> None:
        for observer in self._observers:
            try:
                observer.on_tool_call(result, ctx)
            except Exception:  # observers must never break tool execution
                logger.exception("tool observer failed")


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value
