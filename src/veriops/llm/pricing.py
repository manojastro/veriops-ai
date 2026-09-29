"""Model pricing used for cost estimation (USD per 1M tokens).

These are *illustrative list prices* for estimation and routing comparisons. Verify against the
provider's pricing page for real budgeting, and override with ``LLM_PRICING_OVERRIDES`` (JSON:
``{"model": [input_per_mtok, output_per_mtok]}``).
"""

from __future__ import annotations

import json
import os

DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-opus-5-5": (5.00, 25.00),
    "claude-sonnet-5-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
FALLBACK_PRICE = (1.00, 4.00)


def _overrides() -> dict[str, tuple[float, float]]:
    raw = os.environ.get("LLM_PRICING_OVERRIDES")
    if not raw:
        return {}
    try:
        return {k: (float(v[0]), float(v[1])) for k, v in json.loads(raw).items()}
    except (ValueError, TypeError, KeyError, IndexError):
        return {}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    table = {**DEFAULT_PRICING, **_overrides()}
    price_in, price_out = table.get(model, FALLBACK_PRICE)
    return round(prompt_tokens / 1_000_000 * price_in + completion_tokens / 1_000_000 * price_out, 6)
