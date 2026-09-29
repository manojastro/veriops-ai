"""Token-bucket rate limiting (in-memory; per-process).

For multi-replica deployments swap ``InMemoryRateLimiter`` for a Redis-backed limiter or enforce limits
at the ingress (see infra/kubernetes). The interface is intentionally tiny.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class InMemoryRateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = _Bucket(self.capacity, now)
            bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.updated) * self.rate)
            bucket.updated = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, 0.0
            return False, (cost - bucket.tokens) / self.rate if self.rate else 60.0
