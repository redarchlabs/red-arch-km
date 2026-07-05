"""Unit tests for DELETE /api/memberships/{id} (remove a user from an org)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.repositories.membership import MembershipRepository
from api.routers.memberships import router as memberships_router
from fastapi import FastAPI

ORG_ID = uuid.uuid4()
ADMIN_PROFILE_ID = uuid.uuid4()


def _ctx(*, is_site_admin: bool = True) -> OrgContext:
    user = CurrentUser(
        sub="user_admin",
        username="admin",
        email="admin@example.com",
        profile_id=ADMIN_PROFILE_ID,
        is_site_admin=is_site_admin,
    )
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=True)


def _build_app(ctx: OrgContext) -> FastAPI:
    app = FastAPI()
    app.include_router(memberships_router, prefix="/api/memberships")
    app.dependency_overrides[get_tenant_db] = lambda: AsyncMock()
    app.dependency_overrides[require_org_admin] = lambda: ctx
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _membership(*, profile_id: uuid.UUID | None = None, is_org_admin: bool = False) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        profile_id=profile_id or uuid.uuid4(),
        org_id=ORG_ID,
        is_org_admin=is_org_admin,
    )


def _patch_repo(
    monkeypatch: pytest.MonkeyPatch,
    *,
    membership: Any,
    org_admin_count: int = 2,
) -> list[uuid.UUID]:
    deleted: list[uuid.UUID] = []

    async def _get(self: Any, membership_id: uuid.UUID) -> Any:
        return membership

    async def _count(self: Any, org_id: uuid.UUID) -> int:
        return org_admin_count

    async def _delete(self: Any, membership_id: uuid.UUID) -> bool:
        deleted.append(membership_id)
        return True

    monkeypatch.setattr(MembershipRepository, "get", _get)
    monkeypatch.setattr(MembershipRepository, "count_org_admins", _count, raising=False)
    monkeypatch.setattr(MembershipRepository, "delete", _delete, raising=False)
    return deleted


async def test_delete_membership_204(monkeypatch: pytest.MonkeyPatch) -> None:
    membership = _membership()
    deleted = _patch_repo(monkeypatch, membership=membership)
    async with _client(_build_app(_ctx())) as client:
        resp = await client.delete(f"/api/memberships/{membership.id}")
    assert resp.status_code == 204
    assert deleted == [membership.id]


async def test_delete_membership_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _get_none(self: Any, membership_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(MembershipRepository, "get", _get_none)
    async with _client(_build_app(_ctx())) as client:
        resp = await client.delete(f"/api/memberships/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_org_admin_cannot_remove_own_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    membership = _membership(profile_id=ADMIN_PROFILE_ID)
    deleted = _patch_repo(monkeypatch, membership=membership)
    async with _client(_build_app(_ctx(is_site_admin=False))) as client:
        resp = await client.delete(f"/api/memberships/{membership.id}")
    assert resp.status_code == 400
    assert deleted == []


async def test_site_admin_may_remove_own_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    """Site admins keep synthetic org access, so self-removal can't lock them out."""
    membership = _membership(profile_id=ADMIN_PROFILE_ID)
    deleted = _patch_repo(monkeypatch, membership=membership)
    async with _client(_build_app(_ctx(is_site_admin=True))) as client:
        resp = await client.delete(f"/api/memberships/{membership.id}")
    assert resp.status_code == 204
    assert deleted == [membership.id]


async def test_cannot_remove_last_org_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    membership = _membership(is_org_admin=True)
    deleted = _patch_repo(monkeypatch, membership=membership, org_admin_count=1)
    async with _client(_build_app(_ctx())) as client:
        resp = await client.delete(f"/api/memberships/{membership.id}")
    assert resp.status_code == 409
    assert deleted == []


async def test_can_remove_org_admin_when_another_remains(monkeypatch: pytest.MonkeyPatch) -> None:
    membership = _membership(is_org_admin=True)
    deleted = _patch_repo(monkeypatch, membership=membership, org_admin_count=2)
    async with _client(_build_app(_ctx())) as client:
        resp = await client.delete(f"/api/memberships/{membership.id}")
    assert resp.status_code == 204
    assert deleted == [membership.id]
