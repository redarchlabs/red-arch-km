"""Redis-backed fixed-window rate limiter for the enterprise API surface.

Unlike the in-process :class:`~api.services.rate_limit.SlidingWindowLimiter` (a
per-worker abuse guard for the public form endpoints), API keys are throttled
across all workers via Redis so the quota is a real per-key limit, not a
per-process one.

The implementation is a fixed-window counter: ``INCR`` a per-window bucket key
and let it expire. Fixed windows can admit up to 2× the limit across a window
boundary, which is an accepted trade-off for a coarse quota — it is simple,
atomic, and needs no server-side scripting.

**Fail-open.** If Redis is unavailable the limiter allows the request (and logs a
warning) rather than turning a cache outage into a full API outage.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


async def check_rate_limit(
    redis: Redis,
    key: str,
    *,
    limit: int,
    window_seconds: int = _WINDOW_SECONDS,
    now: float | None = None,
) -> RateLimitResult:
    """Record a hit for ``key`` and report whether it is within ``limit``.

    Returns a :class:`RateLimitResult` with the standard rate-limit headers'
    worth of data. On any Redis error the request is allowed (fail-open).
    """
    current = time.time() if now is None else now
    bucket = int(current // window_seconds)
    retry_after = window_seconds - int(current % window_seconds)
    redis_key = f"ratelimit:{key}:{bucket}"
    try:
        # INCR then always (re)arm the TTL in one round-trip. Doing both atomically
        # avoids two failure modes of a separate INCR-then-EXPIRE: an orphaned key
        # with no TTL if EXPIRE never runs (a slow memory leak), and a bucket that
        # never re-arms expiry if it somehow pre-existed without one.
        pipe = redis.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_seconds)
        count, _ = await pipe.execute()
    except Exception:  # noqa: BLE001 — cache outage must not take down the API
        logger.warning("Rate-limit check failed (allowing request) for key=%s", key, exc_info=True)
        return RateLimitResult(allowed=True, limit=limit, remaining=limit, retry_after=0)

    remaining = max(0, limit - int(count))
    return RateLimitResult(
        allowed=int(count) <= limit,
        limit=limit,
        remaining=remaining,
        retry_after=retry_after if remaining == 0 else 0,
    )
