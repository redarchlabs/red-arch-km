"""A tiny in-process sliding-window rate limiter.

Used to throttle the unauthenticated public form endpoints (keyed by the link
token) so a leaked/known token can't be hammered — the one fully public write
surface. Best-effort and per-process (not shared across workers); it's a basic
abuse guard, not a distributed quota. Memory is bounded by the number of distinct
keys seen within the window.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

_WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    def __init__(self, max_per_window: int) -> None:
        self._max = max(1, max_per_window)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Record a hit for ``key`` and return whether it is within the limit.

        Returns ``False`` (and does not record the hit) once ``max_per_window``
        hits have occurred for this key inside the trailing 60s window."""
        current = time.monotonic() if now is None else now
        cutoff = current - _WINDOW_SECONDS
        hits = self._hits[key]
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            # Drop the empty deque so idle keys don't accumulate forever.
            self._hits.pop(key, None)
            hits = self._hits[key]
        if len(hits) >= self._max:
            return False
        hits.append(current)
        return True
