"""Unit tests for the first-run setup token service (site-admin bootstrap).

The token is the trust anchor for claiming global admin on a fresh install:
generated once per boot while no active site admin exists, printed to the API
logs, stored only as a SHA-256 hash in Redis, and consumed atomically.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from api.services import setup_token as st
from fakeredis import aioredis as fakeaioredis


@pytest.fixture
def redis() -> Any:
    return fakeaioredis.FakeRedis(decode_responses=True)


def _session_with_admin_exists(exists: bool) -> AsyncMock:
    """Mock AsyncSession whose scalar query answers the admin-exists EXISTS."""
    session = AsyncMock()
    result = AsyncMock()
    result.scalar_one.side_effect = None
    result.scalar_one = lambda: exists
    session.execute.return_value = result
    return session


async def test_site_admin_exists_true(redis: Any) -> None:
    assert await st.site_admin_exists(_session_with_admin_exists(True)) is True


async def test_ensure_generates_token_when_no_admin(redis: Any) -> None:
    token = await st.ensure_setup_token(_session_with_admin_exists(False), redis, ttl_seconds=60)
    assert token is not None and len(token) >= 32
    stored = await redis.get(st.TOKEN_KEY)
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert 0 < await redis.ttl(st.TOKEN_KEY) <= 60


async def test_ensure_skips_when_lock_held(redis: Any) -> None:
    """Another worker already won the boot race — don't generate or log twice."""
    await redis.set(st.LOCK_KEY, "1")
    token = await st.ensure_setup_token(_session_with_admin_exists(False), redis, ttl_seconds=60)
    assert token is None


async def test_ensure_deletes_token_when_admin_exists(redis: Any) -> None:
    await redis.set(st.TOKEN_KEY, "stale-hash")
    token = await st.ensure_setup_token(_session_with_admin_exists(True), redis, ttl_seconds=60)
    assert token is None
    assert await redis.get(st.TOKEN_KEY) is None


async def test_consume_accepts_correct_token_once(redis: Any) -> None:
    token = await st.ensure_setup_token(_session_with_admin_exists(False), redis, ttl_seconds=60)
    assert token is not None
    assert await st.consume_setup_token(redis, token) is True
    # Single-use: the DELETE consumed it; a replay must fail.
    assert await st.consume_setup_token(redis, token) is False


async def test_consume_rejects_wrong_token_and_keeps_it(redis: Any) -> None:
    token = await st.ensure_setup_token(_session_with_admin_exists(False), redis, ttl_seconds=60)
    assert token is not None
    assert await st.consume_setup_token(redis, "wrong-" + uuid.uuid4().hex) is False
    # A failed guess must NOT burn the real token.
    assert await st.consume_setup_token(redis, token) is True


async def test_consume_rejects_when_no_token_stored(redis: Any) -> None:
    assert await st.consume_setup_token(redis, "anything") is False


async def test_ensure_never_overwrites_unclaimed_token(redis: Any) -> None:
    """A late-booting worker (or restart) must not invalidate the token the
    operator already copied from the first worker's logs (SET NX)."""
    first = await st.ensure_setup_token(_session_with_admin_exists(False), redis, ttl_seconds=60)
    assert first is not None
    stored_before = await redis.get(st.TOKEN_KEY)

    await redis.delete(st.LOCK_KEY)  # simulate the boot lock expiring
    second = await st.ensure_setup_token(_session_with_admin_exists(False), redis, ttl_seconds=60)
    assert second is None
    assert await redis.get(st.TOKEN_KEY) == stored_before
    # The original token still claims successfully.
    assert await st.consume_setup_token(redis, first) is True
