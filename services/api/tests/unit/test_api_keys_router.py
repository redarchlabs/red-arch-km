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
from api.services.api_key_assignments import AssignmentLabels
from api.services.api_key_service import ApiKeyAssignmentInvalid, ApiKeyConflictError, ApiKeyValidationError
from fastapi import FastAPI, HTTPException, status


def _ctx() -> OrgContext:
    return OrgContext(user=MagicMock(), org_id=uuid.uuid4(), membership=MagicMock(), is_org_admin=True)


def _deny_admin() -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="org admin required")


def _session() -> MagicMock:
    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


def _app(*, admin_ok: bool = True, session: MagicMock | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(api_keys.router, prefix="/api/api-keys")
    app.dependency_overrides[require_org_admin] = _ctx if admin_ok else _deny_admin
    db = session or _session()
    app.dependency_overrides[get_tenant_db] = lambda: db
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
        "access_mode": "org",
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
        svc.assignment_labels = AsyncMock(return_value={})
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post("/api/api-keys/", json={"name": "Integration", "scopes": ["reports:run"]})
        assert resp.status_code == 201
        body = resp.json()
        assert body["key"] == "km2_the_only_time_you_see_this"
        assert body["status"] == "active"
        assert body["key_prefix"] == "km2_AbC123"

    async def test_the_key_is_committed_before_the_plaintext_is_returned(self) -> None:
        """The session teardown commits AFTER the response is sent; a key whose row
        then failed to commit would have handed out a secret for nothing."""
        order: list[str] = []
        session = _session()
        session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
        svc = MagicMock()
        svc.create_key = AsyncMock(return_value=(_key_obj(), "km2_once"))

        async def _labels(_keys: object) -> dict[uuid.UUID, AssignmentLabels]:
            order.append("labels")
            return {}

        svc.assignment_labels = _labels
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app(session=session)) as client:
                resp = await client.post("/api/api-keys/", json={"name": "k", "scopes": ["reports:run"]})
        assert resp.status_code == 201
        assert order == ["commit", "labels"]

    async def test_a_conflict_is_409_and_nothing_is_committed(self) -> None:
        session = _session()
        svc = MagicMock()
        svc.create_key = AsyncMock(side_effect=ApiKeyConflictError("deleted while the key was being created"))
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app(session=session)) as client:
                resp = await client.post(
                    "/api/api-keys/", json={"name": "k", "scopes": ["search:read"], "role_ids": [str(uuid.uuid4())]}
                )
        assert resp.status_code == 409
        assert "key" not in resp.json()
        session.commit.assert_not_awaited()

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
        svc.assignment_labels = AsyncMock(return_value={})
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
        svc.assignment_labels = AsyncMock(return_value={})
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.delete(f"/api/api-keys/{uuid.uuid4()}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"


def _svc(key: SimpleNamespace, labels: dict[uuid.UUID, AssignmentLabels] | None = None) -> MagicMock:
    svc = MagicMock()
    svc.create_key = AsyncMock(return_value=(key, "km2_once"))
    svc.assignment_labels = AsyncMock(return_value=labels or {})
    return svc


class TestScopedKeys:
    async def test_create_passes_assignments_through(self) -> None:
        role, folder = uuid.uuid4(), uuid.uuid4()
        key = _key_obj(access_mode="scoped")
        labels = {key.id: AssignmentLabels(roles=[(role, "Analyst")], folders=[(folder, "Projects.Weekly")])}
        svc = _svc(key, labels)
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post(
                    "/api/api-keys/",
                    json={
                        "name": "Integration",
                        "scopes": ["search:read"],
                        "role_ids": [str(role), str(role)],
                        "folder_ids": [str(folder)],
                    },
                )
        assert resp.status_code == 201, resp.text
        sent = svc.create_key.await_args.kwargs["assignments"]
        assert sent.roles == (role,)  # de-duplicated
        assert sent.folders == (folder,)
        assert sent.regions == sent.groups == sent.departments == ()
        body = resp.json()
        assert body["access_mode"] == "scoped"
        assert body["roles"] == [{"id": str(role), "name": "Analyst"}]
        assert body["folders"] == [{"id": str(folder), "name": "Projects.Weekly"}]
        assert body["regions"] == []

    async def test_create_without_assignments_is_org_wide(self) -> None:
        svc = _svc(_key_obj())
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post("/api/api-keys/", json={"name": "k", "scopes": ["search:read"]})
        assert resp.status_code == 201
        assert svc.create_key.await_args.kwargs["assignments"].is_empty
        assert resp.json()["access_mode"] == "org"

    async def test_invalid_assignment_is_422(self) -> None:
        svc = MagicMock()
        svc.create_key = AsyncMock(side_effect=ApiKeyAssignmentInvalid("Unknown role id(s) in this organization: x"))
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post(
                    "/api/api-keys/",
                    json={"name": "k", "scopes": ["search:read"], "role_ids": [str(uuid.uuid4())]},
                )
        assert resp.status_code == 422
        assert "Unknown role" in resp.json()["detail"]

    async def test_profile_id_is_no_longer_accepted(self) -> None:
        async with _client(_app()) as client:
            resp = await client.post(
                "/api/api-keys/", json={"name": "k", "scopes": ["search:read"], "profile_id": str(uuid.uuid4())}
            )
        assert resp.status_code == 422

    async def test_bindable_profiles_route_is_gone(self) -> None:
        async with _client(_app()) as client:
            resp = await client.get("/api/api-keys/bindable-profiles")
        assert resp.status_code in (404, 405, 422)


class TestAssignmentsNote:
    """A revoked or expired key's assignment rows are released when one of its
    items is deleted; the list must not then show it as a BROKEN key."""

    async def _list(self, *keys: SimpleNamespace, labels: dict | None = None) -> list[dict]:
        svc = MagicMock()
        svc.list_keys = AsyncMock(return_value=list(keys))
        svc.assignment_labels = AsyncMock(return_value=labels or {})
        with patch.object(api_keys, "ApiKeyService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.get("/api/api-keys/")
        assert resp.status_code == 200
        return resp.json()

    async def test_revoked_scoped_key_without_rows_says_released(self) -> None:
        (row,) = await self._list(_key_obj(access_mode="scoped", revoked_at=datetime.now(UTC)))
        assert row["assignments_note"] is not None
        assert "revoked" in row["assignments_note"]
        assert "refused" not in row["assignments_note"]

    async def test_expired_scoped_key_without_rows_says_released(self) -> None:
        (row,) = await self._list(_key_obj(access_mode="scoped", expires_at=datetime(2020, 1, 1, tzinfo=UTC)))
        assert "expired" in row["assignments_note"]

    async def test_active_scoped_key_without_rows_is_broken(self) -> None:
        (row,) = await self._list(_key_obj(access_mode="scoped"))
        assert "refused" in row["assignments_note"]

    async def test_keys_with_rows_and_org_keys_have_no_note(self) -> None:
        scoped = _key_obj(access_mode="scoped")
        labels = {scoped.id: AssignmentLabels(roles=[(uuid.uuid4(), "Analyst")])}
        rows = await self._list(scoped, _key_obj(), labels=labels)
        assert [r["assignments_note"] for r in rows] == [None, None]
