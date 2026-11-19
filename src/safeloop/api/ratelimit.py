"""In-memory token-bucket rate limiter (per client key). Single-process only: use a reverse proxy
or shared store for multi-instance deployments."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class RateLimiter:
    def __init__(self, per_minute: int, burst: int | None = None, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000):
        self.rate = per_minute / 60.0
        self.burst = float(burst if burst is not None else max(1, per_minute))
        self._clock, self._max_keys = clock, max_keys
        self._b: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, last)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.rate > 0

    def check(self, key: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        if not self.enabled:
            return True, 0.0
        now = self._clock()
        with self._lock:
            tokens, last = self._b.pop(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._b[key] = (tokens, now)
            while len(self._b) > self._max_keys:  # bound memory
                self._b.popitem(last=False)
            return allowed, 0.0 if allowed else (1.0 - tokens) / self.rate
