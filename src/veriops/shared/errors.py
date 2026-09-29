"""Typed error hierarchy used across the platform."""

from __future__ import annotations


class VeriOpsError(Exception):
    """Base class for all platform errors."""


class ToolError(VeriOpsError):
    """A tool invocation failed (after retries)."""

    def __init__(self, tool: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.retryable = retryable


class ToolTimeoutError(ToolError):
    def __init__(self, tool: str, timeout_s: float) -> None:
        super().__init__(tool, f"timed out after {timeout_s:.1f}s", retryable=True)


class ToolPermissionError(ToolError):
    """The caller is not allowed to invoke this tool (permission class or allow-list)."""


class LLMError(VeriOpsError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMOutputValidationError(LLMError):
    """The model returned output that does not match the requested schema."""


class PolicyViolationError(VeriOpsError):
    """An action was blocked by the policy engine."""


class NotFoundError(VeriOpsError):
    pass


class ConflictError(VeriOpsError):
    pass
