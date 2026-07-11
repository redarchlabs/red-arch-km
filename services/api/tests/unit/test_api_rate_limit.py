"""Unit tests for the Redis fixed-window rate limiter used by the enterprise API."""

from __future__ import annotations

from unittest.mock import MagicMock

from api.services.api_rate_limit import check_rate_limit


class _FakePipe:
    """Minimal stand-in for a redis pipeline: records INCR/EXPIRE, returns [count, True]."""

    def __init__(self, count: int) -> None:
        self._count = count
        self.incr_called = False
        self.expire_called = False

    def incr(self, key: str) -> None:
        self.incr_called = True

    def expire(self, key: str, ttl: int) -> None:
        self.expire_called = True

    async def execute(self) -> list:
        return [self._count, True]


def _redis(pipe: object) -> MagicMock:
    r = MagicMock()
    r.pipeline = MagicMock(return_value=pipe)
    return r


class TestCheckRateLimit:
    async def test_within_limit_allows_and_arms_ttl(self) -> None:
        pipe = _FakePipe(count=1)
        result = await check_rate_limit(_redis(pipe), "k", limit=5, now=0.0)
        assert result.allowed is True
        assert result.remaining == 4
        # TTL is always (re)armed in the same pipeline — no orphaned no-TTL bucket.
        assert pipe.incr_called and pipe.expire_called

    async def test_at_limit_still_allowed(self) -> None:
        result = await check_rate_limit(_redis(_FakePipe(count=5)), "k", limit=5, now=0.0)
        assert result.allowed is True
        assert result.remaining == 0

    async def test_over_limit_blocked_with_retry_after(self) -> None:
        # now=10s into a 60s window → retry_after = 50.
        result = await check_rate_limit(_redis(_FakePipe(count=6)), "k", limit=5, now=10.0)
        assert result.allowed is False
        assert result.remaining == 0
        assert result.retry_after == 50

    async def test_fail_open_on_redis_error(self) -> None:
        broken = MagicMock()
        broken.pipeline = MagicMock(side_effect=RuntimeError("redis down"))
        result = await check_rate_limit(broken, "k", limit=5)
        # A cache outage must not throttle traffic — fail open.
        assert result.allowed is True
        assert result.remaining == 5
