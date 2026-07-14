"""HTTP-level tests for the inbound config-promotion receiver (/api/v1/config).

Verifies the scope wiring (config:read for ping, the sensitive config:write for the
apply) and that a valid bundle is applied via the executor — mocking the executor
+ session so no DB is needed, mirroring tests/unit/test_v1_routers.py.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth import api_key as ak
from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_owner_db, require_api_key
from api.dependencies import get_redis
from api.routers import v1 as v1_router
from api.routers.v1 import config as v1_config
from api.services.api_rate_limit import RateLimitResult
from api.services.migration.diff import BundleDiff
from api.services.migration.promotion import PromotionResult
from fastapi import FastAPI

pytestmark = pytest.mark.unit

_BUNDLE = {"kind": "km2-migration-bundle", "format_version": 2, "resources": {}}
_ALLOWED = RateLimitResult(allowed=True, limit=600, remaining=599, retry_after=0)


@pytest.fixture(autouse=True)
def _allow_rate_limit():  # noqa: ANN202
    with patch.object(ak, "check_rate_limit", AsyncMock(return_value=_ALLOWED)):
        yield


def _principal(scopes: set[str]) -> ApiKeyPrincipal:
    return ApiKeyPrincipal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), scopes=frozenset(scopes), name="k")


def _app(scopes: set[str]) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_router.router, prefix="/api/v1")
    app.dependency_overrides[require_api_key] = lambda: _principal(scopes)
    app.dependency_overrides[get_apikey_tenant_owner_db] = lambda: AsyncMock()
    app.dependency_overrides[get_redis] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _upload() -> dict:
    return {"file": ("bundle.json", json.dumps(_BUNDLE), "application/json")}


async def test_ping_requires_config_read() -> None:
    async with _client(_app(set())) as c:
        resp = await c.get("/api/v1/config/ping")
    assert resp.status_code == 403


async def test_ping_reports_bundle_version() -> None:
    async with _client(_app({"config:read"})) as c:
        resp = await c.get("/api/v1/config/ping")
    assert resp.status_code == 200
    assert resp.json()["bundle_format_version"] == 2


async def test_receive_requires_config_write() -> None:
    # A config:read key (or a wildcard, tested in scopes) must NOT reach the apply.
    async with _client(_app({"config:read"})) as c:
        resp = await c.post("/api/v1/config/promotions", files=_upload())
    assert resp.status_code == 403


async def test_receive_applies_with_config_write() -> None:
    result = PromotionResult(diff=BundleDiff())
    executor = MagicMock()
    executor.promote = AsyncMock(return_value=(result, AsyncMock()))
    with patch.object(v1_config, "PromotionExecutor", return_value=executor):
        async with _client(_app({"config:write"})) as c:
            resp = await c.post("/api/v1/config/promotions", files=_upload())
    assert resp.status_code == 200
    executor.promote.assert_awaited_once()
