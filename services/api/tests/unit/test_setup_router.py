"""Unit tests for the /api/setup router (first-run bootstrap wizard backend)."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, get_current_user
from api.config import Settings, get_settings
from api.dependencies import get_db, get_redis
from api.models.user import UserProfile
from api.routers import setup as setup_router_module
from api.routers.setup import router as setup_router
from api.services.setup_token import TOKEN_KEY
from fakeredis import aioredis as fakeaioredis
from fastapi import FastAPI


@pytest.fixture
def redis() -> Any:
    return fakeaioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def profile() -> UserProfile:
    return UserProfile(
        id=uuid.uuid4(),
        auth_subject="user_clerk_abc",
        username="alice",
        email="alice@example.com",
        is_site_admin=False,
        is_active=True,
    )


def _build_app(redis: Any, profile: UserProfile | None) -> FastAPI:
    app = FastAPI()
    app.include_router(setup_router, prefix="/api/setup")

    session = AsyncMock()
    session.get.return_value = profile

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    if profile is not None:
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            sub=profile.auth_subject,
            username=profile.username,
            email=profile.email,
            profile_id=profile.id,
            is_site_admin=profile.is_site_admin,
        )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _force_admin_exists(monkeypatch: pytest.MonkeyPatch, exists: bool) -> None:
    async def _fake(session: Any) -> bool:
        return exists

    monkeypatch.setattr(setup_router_module, "site_admin_exists", _fake)


async def _store_token(redis: Any, token: str) -> None:
    await redis.set(TOKEN_KEY, hashlib.sha256(token.encode()).hexdigest())


async def test_status_needs_setup_true(redis: Any, profile: UserProfile, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_admin_exists(monkeypatch, False)
    async with _client(_build_app(redis, profile)) as client:
        resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"needs_setup": True}


async def test_status_needs_setup_false(redis: Any, profile: UserProfile, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_admin_exists(monkeypatch, True)
    async with _client(_build_app(redis, profile)) as client:
        resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"needs_setup": False}


async def test_status_requires_no_auth(redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Status must be reachable pre-login (the wizard checks it before Clerk)."""
    _force_admin_exists(monkeypatch, False)
    async with _client(_build_app(redis, profile=None)) as client:
        resp = await client.get("/api/setup/status")
    assert resp.status_code == 200


async def test_claim_happy_path(redis: Any, profile: UserProfile, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_admin_exists(monkeypatch, False)
    await _store_token(redis, "tok-123")
    async with _client(_build_app(redis, profile)) as client:
        resp = await client.post("/api/setup/claim", json={"token": "tok-123"})
    assert resp.status_code == 200
    assert resp.json() == {"claimed": True}
    assert profile.is_site_admin is True
    # Token is single-use: consumed on success.
    assert await redis.get(TOKEN_KEY) is None


async def test_claim_wrong_token_403(redis: Any, profile: UserProfile, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_admin_exists(monkeypatch, False)
    await _store_token(redis, "tok-123")
    async with _client(_build_app(redis, profile)) as client:
        resp = await client.post("/api/setup/claim", json={"token": "wrong"})
    assert resp.status_code == 403
    assert profile.is_site_admin is False


async def test_claim_when_admin_exists_409(redis: Any, profile: UserProfile, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_admin_exists(monkeypatch, True)
    await _store_token(redis, "tok-123")
    async with _client(_build_app(redis, profile)) as client:
        resp = await client.post("/api/setup/claim", json={"token": "tok-123"})
    assert resp.status_code == 409
    # Lingering token is cleaned up once an admin exists.
    assert await redis.get(TOKEN_KEY) is None


async def test_claim_restores_token_when_promotion_fails(
    redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DB failure after the token is consumed must put the token back —
    otherwise setup is bricked until the next restart with no explanation."""
    _force_admin_exists(monkeypatch, False)
    await _store_token(redis, "tok-123")
    # profile=None makes the promotion step raise RuntimeError after consume.
    app = _build_app(redis, profile=None)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        sub="user_x",
        username="x",
        email="x@example.com",
        profile_id=uuid.uuid4(),
        is_site_admin=False,
    )
    async with _client(app) as client:
        with pytest.raises(RuntimeError):
            await client.post("/api/setup/claim", json={"token": "tok-123"})
    assert await redis.get(TOKEN_KEY) is not None
    # And the restored token is still claimable.
    from api.services.setup_token import consume_setup_token

    assert await consume_setup_token(redis, "tok-123") is True


async def test_claim_requires_auth(redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an authenticated user the claim must 401 before touching the token."""
    _force_admin_exists(monkeypatch, False)
    await _store_token(redis, "tok-123")
    async with _client(_build_app(redis, profile=None)) as client:
        resp = await client.post("/api/setup/claim", json={"token": "tok-123"})
    assert resp.status_code == 401
    assert await redis.get(TOKEN_KEY) is not None
