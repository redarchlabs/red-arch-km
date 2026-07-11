"""Unit tests for the API-key management router (Admin Area surface).

Covers the org-admin gate, the create→one-time-plaintext contract, metadata-only
reads, revoke, the scope catalog, and validation-error mapping. ApiKeyService is
mocked so no database is required (mirrors test_reports_router.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.routers import api_keys
from api.services.api_key_service import ApiKeyValidationError
from fastapi import FastAPI, HTTPException, status


def _ctx() -> OrgContext:
    return OrgContext(user=MagicMock(), org_id=uuid.uuid4(), membership=MagicMock(), is_org_admin=True)


def _deny_admin() -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="org admin required")


def _app(*, admin_ok: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(api_keys.router, prefix="/api/api-keys")
    app.dependency_overrides[require_org_admin] = _ctx if admin_ok else _deny_admin
    app.dependency_overrides[get_tenant_db] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _key_obj(**over: object) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "name": "Integration",
        "key_prefix": "km2_AbC123",
        "scopes": ["reports:run"],
        "created_by_profile_id": None,
        "last_used_at": None,
        "expires_at": None,
        "revoked_at": None,
        "created_at": datetime.now(UTC),
    }
    base.update(over)
    return SimpleNamespace(**base)


class TestAdminGate:
    async def test_non_admin_cannot_create(self) -> None:
        async with _client(_app(admin_ok=False)) as client:
            resp = await client.post("/api/api-keys/", json={"name": "k", "scopes": ["reports:run"]})
        assert resp.status_code == 403

    async def test_non_admin_cannot_list(self) -> None:
        async with _client(_app(admin_ok=False)) as client:
            resp = await client.get("/api/api-keys/")
        assert resp.status_code == 403


class TestScopes:
    async def test_scope_catalog_returned(self) -> None:
        async with _client(_app()) as client:
            resp = await client.get("/api/api-keys/scopes")
        assert resp.status_code == 200
        names = {s["name"] for s in resp.json()}
        assert "reports:run" in names and "records:write" in names


class TestCreate:
    async def test_create_returns_plaintext_once(self) -> None:
        svc = MagicMock()
        svc.create_key = AsyncMock(return_value=(_key_obj(), "km2_the_only_time_you_see_this"))
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post(
                    "/api/api-keys/", json={"name": "Integration", "scopes": ["reports:run"]}
                )
        assert resp.status_code == 201
        body = resp.json()
        assert body["key"] == "km2_the_only_time_you_see_this"
        assert body["status"] == "active"
        assert body["key_prefix"] == "km2_AbC123"

    async def test_validation_error_is_400(self) -> None:
        svc = MagicMock()
        svc.create_key = AsyncMock(side_effect=ApiKeyValidationError("At least one scope is required"))
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post("/api/api-keys/", json={"name": "k", "scopes": ["reports:run"]})
        assert resp.status_code == 400


class TestListAndRevoke:
    async def test_list_never_exposes_secret(self) -> None:
        svc = MagicMock()
        svc.list_keys = AsyncMock(return_value=[_key_obj(), _key_obj(revoked_at=datetime.now(UTC))])
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.get("/api/api-keys/")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        assert all("key" not in row for row in rows)  # metadata only
        statuses = {row["status"] for row in rows}
        assert statuses == {"active", "revoked"}

    async def test_revoke_returns_updated_metadata(self) -> None:
        svc = MagicMock()
        svc.revoke_key = AsyncMock(return_value=_key_obj(revoked_at=datetime.now(UTC)))
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.delete(f"/api/api-keys/{uuid.uuid4()}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"
