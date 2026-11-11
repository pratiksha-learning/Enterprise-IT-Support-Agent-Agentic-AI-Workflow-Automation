"""In-memory token-bucket rate limiter.

Per-instance only; a multi-instance deployment should back this with Redis or use
Azure API Management rate-limit-by-key policies in front of the API.
"""
from __future__ import annotations

import threading
import time


class TokenBucketLimiter:
    def __init__(self, rate_per_minute: int, burst: int):
        self.rate = rate_per_minute / 60.0
        self.capacity = float(burst)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= cost:
                self._buckets[key] = (tokens - cost, now)
                return True, 0.0
            self._buckets[key] = (tokens, now)
            return False, round((cost - tokens) / self.rate, 2) if self.rate else 60.0

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
