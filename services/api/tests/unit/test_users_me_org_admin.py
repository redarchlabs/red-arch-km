"""Unit tests for the per-org admin flag on GET /api/users/me.

The `orgs` array now carries `is_admin` so the frontend can hide admin-only
surfaces (ingest log, reprocess) from non-admin members. Site admins administer
every org; regular users administer only orgs where their membership is
org-admin. The OrgRepository is monkeypatched, so these run without a database.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from api.auth.dependencies import CurrentUser, get_current_user
from api.dependencies import get_db
from api.routers import users as users_module
from api.routers.users import router as users_router
from fastapi import FastAPI

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
PROFILE_ID = uuid.uuid4()


class _Org:
    def __init__(self, org_id: uuid.UUID, name: str) -> None:
        self.id = org_id
        self.name = name


def _user(*, is_site_admin: bool) -> CurrentUser:
    return CurrentUser(
        sub="user_x",
        username="x",
        email="x@example.com",
        profile_id=PROFILE_ID,
        is_site_admin=is_site_admin,
    )


def _build_app(user: CurrentUser) -> FastAPI:
    app = FastAPI()
    app.include_router(users_router, prefix="/api/users")

    async def _fake_db() -> Any:
        yield object()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _fake_db
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class _FakeOrgRepo:
    """Stub OrgRepository: user is a member of A and B, admin of A only."""

    def __init__(self, _session: Any) -> None: ...

    async def list_for_user(self, _profile_id: uuid.UUID, *, limit: int = 200) -> tuple[list[_Org], int]:
        orgs = [_Org(ORG_A, "Alpha"), _Org(ORG_B, "Beta")]
        return orgs, len(orgs)

    async def list_all(self, *, limit: int = 200) -> tuple[list[_Org], int]:
        orgs = [_Org(ORG_A, "Alpha"), _Org(ORG_B, "Beta")]
        return orgs, len(orgs)

    async def admin_org_ids(self, _profile_id: uuid.UUID) -> set[uuid.UUID]:
        return {ORG_A}


async def test_me_marks_only_admin_orgs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(users_module, "OrgRepository", _FakeOrgRepo)

    async with _client(_build_app(_user(is_site_admin=False))) as client:
        resp = await client.get("/api/users/me")

    assert resp.status_code == 200
    orgs = {o["id"]: o["is_admin"] for o in resp.json()["orgs"]}
    assert orgs == {str(ORG_A): True, str(ORG_B): False}


async def test_me_site_admin_is_admin_of_every_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(users_module, "OrgRepository", _FakeOrgRepo)

    async with _client(_build_app(_user(is_site_admin=True))) as client:
        resp = await client.get("/api/users/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_site_admin"] is True
    assert all(o["is_admin"] for o in body["orgs"])
