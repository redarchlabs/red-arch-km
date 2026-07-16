"""Unit tests for the API-key auth dependencies.

Covers key resolution (valid / revoked / expired / missing / wrong-scheme),
scope enforcement, and the per-key rate limiter — the security-critical gate on
the ``/api/v1`` surface. No database: the hash lookup + rate-limit backend are
patched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth import api_key as ak
from api.auth.api_key import ApiKeyPrincipal
from api.dependencies import get_redis
from api.services.api_rate_limit import RateLimitResult
from fastapi import Depends, FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class _FakeSession:
    """Async-context session stand-in for the short-lived auth session that
    require_api_key opens itself (via get_session_factory, not get_db)."""

    def __init__(self) -> None:
        self.execute = AsyncMock()
        self.commit = AsyncMock()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _fake_auth_session():  # noqa: ANN202
    """require_api_key opens its own session; give it a mock so the touch/commit
    never hit a real DB."""
    with patch.object(ak, "get_session_factory", lambda _settings: (lambda: _FakeSession())):
        yield


def _fake_key(**over: object) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "org_id": uuid.uuid4(),
        "scopes": ["reports:run"],
        "name": "k",
        "revoked_at": None,
        "expires_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _app_require_key() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(principal: Annotated[ApiKeyPrincipal, Depends(ak.require_api_key)]):  # noqa: ANN202
        return {"org": str(principal.org_id), "scopes": sorted(principal.scopes)}

    return app


class TestRequireApiKey:
    async def test_valid_bearer_key(self) -> None:
        key = _fake_key()
        with patch.object(ak, "lookup_by_key_hash", AsyncMock(return_value=key)):
            async with _client(_app_require_key()) as client:
                resp = await client.get("/probe", headers={"Authorization": "Bearer km2_secret"})
        assert resp.status_code == 200
        assert resp.json()["scopes"] == ["reports:run"]

    async def test_valid_x_api_key_header(self) -> None:
        key = _fake_key()
        with patch.object(ak, "lookup_by_key_hash", AsyncMock(return_value=key)):
            async with _client(_app_require_key()) as client:
                resp = await client.get("/probe", headers={"X-API-Key": "km2_secret"})
        assert resp.status_code == 200

    async def test_missing_key_is_401(self) -> None:
        async with _client(_app_require_key()) as client:
            resp = await client.get("/probe")
        assert resp.status_code == 401

    async def test_wrong_scheme_prefix_is_401(self) -> None:
        # A non-km2 bearer (e.g. a Clerk JWT) must not be accepted here.
        async with _client(_app_require_key()) as client:
            resp = await client.get("/probe", headers={"Authorization": "Bearer eyJ.jwt.token"})
        assert resp.status_code == 401

    async def test_unknown_key_is_401(self) -> None:
        with patch.object(ak, "lookup_by_key_hash", AsyncMock(return_value=None)):
            async with _client(_app_require_key()) as client:
                resp = await client.get("/probe", headers={"Authorization": "Bearer km2_nope"})
        assert resp.status_code == 401

    async def test_revoked_key_is_401(self) -> None:
        key = _fake_key(revoked_at=datetime.now(UTC))
        with patch.object(ak, "lookup_by_key_hash", AsyncMock(return_value=key)):
            async with _client(_app_require_key()) as client:
                resp = await client.get("/probe", headers={"Authorization": "Bearer km2_x"})
        assert resp.status_code == 401

    async def test_expired_key_is_401(self) -> None:
        key = _fake_key(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        with patch.object(ak, "lookup_by_key_hash", AsyncMock(return_value=key)):
            async with _client(_app_require_key()) as client:
                resp = await client.get("/probe", headers={"Authorization": "Bearer km2_x"})
        assert resp.status_code == 401


def _principal(scopes: set[str]) -> ApiKeyPrincipal:
    return ApiKeyPrincipal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), scopes=frozenset(scopes), name="k")


class TestRequireScope:
    def _app(self, scopes: set[str]) -> FastAPI:
        app = FastAPI()

        @app.get("/need")
        async def need(p: Annotated[ApiKeyPrincipal, Depends(ak.require_scope("reports:run"))]):  # noqa: ANN202
            return {"ok": True}

        app.dependency_overrides[ak.require_api_key] = lambda: _principal(scopes)
        return app

    async def test_scope_present_allows(self) -> None:
        async with _client(self._app({"reports:run"})) as client:
            resp = await client.get("/need")
        assert resp.status_code == 200

    async def test_scope_missing_is_403(self) -> None:
        async with _client(self._app({"reports:read"})) as client:
            resp = await client.get("/need")
        assert resp.status_code == 403


class TestRateLimit:
    def _app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/rl", dependencies=[Depends(ak.enforce_api_rate_limit)])
        async def rl():  # noqa: ANN202
            return {"ok": True}

        app.dependency_overrides[ak.require_api_key] = lambda: _principal({"reports:run"})
        app.dependency_overrides[get_redis] = lambda: MagicMock()
        return app

    async def test_within_limit_sets_headers(self) -> None:
        allowed = RateLimitResult(allowed=True, limit=600, remaining=599, retry_after=0)
        with patch.object(ak, "check_rate_limit", AsyncMock(return_value=allowed)):
            async with _client(self._app()) as client:
                resp = await client.get("/rl")
        assert resp.status_code == 200
        assert resp.headers["X-RateLimit-Limit"] == "600"
        assert resp.headers["X-RateLimit-Remaining"] == "599"

    async def test_over_limit_is_429_with_retry_after(self) -> None:
        blocked = RateLimitResult(allowed=False, limit=600, remaining=0, retry_after=42)
        with patch.object(ak, "check_rate_limit", AsyncMock(return_value=blocked)):
            async with _client(self._app()) as client:
                resp = await client.get("/rl")
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "42"


class TestIpRateLimit:
    """enforce_ip_rate_limit: the pre-auth, per-client-IP throttle."""

    def _app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/rl", dependencies=[Depends(ak.enforce_ip_rate_limit)])
        async def rl():  # noqa: ANN202
            return {"ok": True}

        app.dependency_overrides[get_redis] = lambda: MagicMock()
        return app

    async def test_within_limit_allows(self) -> None:
        allowed = RateLimitResult(allowed=True, limit=1200, remaining=1199, retry_after=0)
        limiter = AsyncMock(return_value=allowed)
        with patch.object(ak, "check_rate_limit", limiter):
            async with _client(self._app()) as client:
                resp = await client.get("/rl")
        assert resp.status_code == 200
        # Keyed by client IP, not by API key — it must run before key resolution.
        assert limiter.await_args.args[1].startswith("ip:")

    async def test_over_limit_is_429_with_retry_after(self) -> None:
        blocked = RateLimitResult(allowed=False, limit=1200, remaining=0, retry_after=17)
        with patch.object(ak, "check_rate_limit", AsyncMock(return_value=blocked)):
            async with _client(self._app()) as client:
                resp = await client.get("/rl")
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "17"

    async def test_requires_no_api_key(self) -> None:
        """The IP throttle must engage without any Authorization header at all —
        that is its purpose: bounding floods of invalid/missing keys."""
        blocked = RateLimitResult(allowed=False, limit=1200, remaining=0, retry_after=9)
        with patch.object(ak, "check_rate_limit", AsyncMock(return_value=blocked)):
            async with _client(self._app()) as client:
                resp = await client.get("/rl")  # no auth header
        assert resp.status_code == 429  # throttled, not 401


class TestV1RouterWiring:
    """Both limiters must be attached to the /api/v1 router, IP throttle first
    (it is documented to run BEFORE key resolution)."""

    def test_ip_and_key_limiters_attached_in_order(self) -> None:
        from api.routers.v1 import router as v1_router

        deps = [d.dependency for d in v1_router.dependencies]
        assert ak.enforce_ip_rate_limit in deps, "per-IP limiter not wired on /api/v1"
        assert ak.enforce_api_rate_limit in deps, "per-key limiter not wired on /api/v1"
        assert deps.index(ak.enforce_ip_rate_limit) < deps.index(ak.enforce_api_rate_limit), (
            "IP throttle must run before per-key limiter (pre-auth)"
        )
