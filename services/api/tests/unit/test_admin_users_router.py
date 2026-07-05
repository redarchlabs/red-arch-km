"""Unit tests for the site-admin user-management endpoints (/api/admin/users)."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, get_current_user
from api.dependencies import get_db
from api.models.user import UserProfile
from api.repositories.user import UserRepository
from api.routers.admin import router as admin_router
from fastapi import FastAPI

ADMIN_ID = uuid.uuid4()
TARGET_ID = uuid.uuid4()


def _admin_user(*, is_site_admin: bool = True) -> CurrentUser:
    return CurrentUser(
        sub="user_admin",
        username="admin",
        email="admin@example.com",
        profile_id=ADMIN_ID,
        is_site_admin=is_site_admin,
    )


def _target_profile(**overrides: Any) -> UserProfile:
    defaults: dict[str, Any] = {
        "id": TARGET_ID,
        "auth_subject": "user_target",
        "username": "bob",
        "email": "bob@example.com",
        "description": None,
        "is_site_admin": False,
        "is_active": True,
    }
    defaults.update(overrides)
    return UserProfile(**defaults)


def _build_app(current: CurrentUser) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/admin")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: current
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def patch_repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch UserRepository data-access; tests adjust the returned dict."""
    state: dict[str, Any] = {
        "users": [_target_profile()],
        "total": 1,
        "target": _target_profile(),
        "active_admin_count": 2,
    }

    async def _list_all(self: Any, *, offset: int = 0, limit: int = 50, q: str | None = None) -> Any:
        state["last_query"] = q
        return state["users"], state["total"]

    async def _get(self: Any, profile_id: uuid.UUID) -> Any:
        return state["target"] if state["target"] is not None and profile_id == TARGET_ID else None

    async def _count(self: Any) -> int:
        return state["active_admin_count"]

    monkeypatch.setattr(UserRepository, "list_all", _list_all, raising=False)
    monkeypatch.setattr(UserRepository, "get", _get)
    monkeypatch.setattr(UserRepository, "count_active_site_admins", _count, raising=False)
    return state


async def test_list_users_requires_site_admin(patch_repo: dict[str, Any]) -> None:
    async with _client(_build_app(_admin_user(is_site_admin=False))) as client:
        resp = await client.get("/api/admin/users")
    assert resp.status_code == 403


async def test_list_users_returns_page(patch_repo: dict[str, Any]) -> None:
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.get("/api/admin/users", params={"q": "bo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["username"] == "bob"
    assert body["items"][0]["is_active"] is True
    assert patch_repo["last_query"] == "bo"


async def test_patch_promotes_user(patch_repo: dict[str, Any]) -> None:
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(f"/api/admin/users/{TARGET_ID}", json={"is_site_admin": True})
    assert resp.status_code == 200
    assert resp.json()["is_site_admin"] is True
    assert patch_repo["target"].is_site_admin is True


async def test_patch_deactivates_user(patch_repo: dict[str, Any]) -> None:
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(f"/api/admin/users/{TARGET_ID}", json={"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_patch_unknown_user_404(patch_repo: dict[str, Any]) -> None:
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(f"/api/admin/users/{uuid.uuid4()}", json={"is_active": False})
    assert resp.status_code == 404


async def test_patch_rejects_self_demotion(patch_repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin must not demote or deactivate themselves (lock-out guard)."""
    self_profile = _target_profile(id=ADMIN_ID, is_site_admin=True)
    patch_repo["target"] = self_profile

    async def _get_self(self: Any, profile_id: uuid.UUID) -> Any:
        return self_profile if profile_id == ADMIN_ID else None

    monkeypatch.setattr(UserRepository, "get", _get_self)
    async with _client(_build_app(_admin_user())) as client:
        for body in ({"is_site_admin": False}, {"is_active": False}):
            resp = await client.patch(f"/api/admin/users/{ADMIN_ID}", json=body)
            assert resp.status_code == 400


async def test_patch_rejects_demoting_last_active_admin(patch_repo: dict[str, Any]) -> None:
    patch_repo["target"] = _target_profile(is_site_admin=True, is_active=True)
    patch_repo["active_admin_count"] = 1
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(f"/api/admin/users/{TARGET_ID}", json={"is_site_admin": False})
        assert resp.status_code == 409
        resp = await client.patch(f"/api/admin/users/{TARGET_ID}", json={"is_active": False})
        assert resp.status_code == 409


async def test_patch_allows_demoting_admin_when_another_remains(patch_repo: dict[str, Any]) -> None:
    patch_repo["target"] = _target_profile(is_site_admin=True, is_active=True)
    patch_repo["active_admin_count"] = 2
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(f"/api/admin/users/{TARGET_ID}", json={"is_site_admin": False})
    assert resp.status_code == 200
    assert resp.json()["is_site_admin"] is False


async def test_patch_combined_flags_in_one_request(patch_repo: dict[str, Any]) -> None:
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(
            f"/api/admin/users/{TARGET_ID}", json={"is_site_admin": True, "is_active": False}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_site_admin"] is True
    assert body["is_active"] is False


async def test_patch_combined_demote_and_deactivate_last_admin_409(patch_repo: dict[str, Any]) -> None:
    patch_repo["target"] = _target_profile(is_site_admin=True, is_active=True)
    patch_repo["active_admin_count"] = 1
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(
            f"/api/admin/users/{TARGET_ID}", json={"is_site_admin": False, "is_active": False}
        )
    assert resp.status_code == 409
    # Guard must reject before either flag is applied.
    assert patch_repo["target"].is_site_admin is True
    assert patch_repo["target"].is_active is True


async def test_list_user_memberships_404_for_unknown_user(patch_repo: dict[str, Any]) -> None:
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.get(f"/api/admin/users/{uuid.uuid4()}/memberships")
    assert resp.status_code == 404


async def test_list_user_memberships_returns_summaries(
    patch_repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    org_id = uuid.uuid4()
    membership_id = uuid.uuid4()

    async def _rows(self: Any, profile_id: uuid.UUID) -> Any:
        return [
            (
                SimpleNamespace(id=membership_id, is_org_admin=True),
                SimpleNamespace(id=org_id, name="Acme"),
            )
        ]

    monkeypatch.setattr(UserRepository, "list_memberships_with_orgs", _rows, raising=False)
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.get(f"/api/admin/users/{TARGET_ID}/memberships")
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "membership_id": str(membership_id),
            "org_id": str(org_id),
            "org_name": "Acme",
            "is_org_admin": True,
        }
    ]


async def test_patch_rejects_unknown_fields(patch_repo: dict[str, Any]) -> None:
    """Username/email are Clerk-owned — the schema must refuse them."""
    async with _client(_build_app(_admin_user())) as client:
        resp = await client.patch(f"/api/admin/users/{TARGET_ID}", json={"username": "evil"})
    assert resp.status_code == 422
