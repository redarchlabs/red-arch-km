"""First-party document reads honour folder permissions (real PostgreSQL).

``GET /api/documents/{id}`` and its ``/content``, ``/chunks``, ``/summary``,
``/logs`` and ``/by-key`` siblings used to resolve a document by id alone, so a
member who learned a restricted document's UUID (a shared link, a citation, a log
line) could read it. They now apply the same visibility rule as the member list
and the public API: filed in a folder the member can see, and admitted by the
document's own viewer override if it has one. Admins are unaffected.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from access_mask import MAX_GROUP, MAX_REGION, MAX_ROLE, encode
from api import db_scope
from api.auth.dependencies import CurrentUser, OrgContext, require_org_access
from api.dependencies import get_redis, get_tenant_db
from api.models.document import Document, Folder
from api.models.org import Org
from api.models.user import UserOrgMembership, UserProfile
from api.routers import documents as documents_router
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

pytestmark = pytest.mark.integration

_SUFFIXES = ["", "/content", "/chunks", "/summary", "/logs"]


@pytest_asyncio.fixture
async def seed_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    seed_engine = create_async_engine(database_url)
    yield async_sessionmaker(seed_engine, expire_on_commit=False)
    await seed_engine.dispose()


@pytest_asyncio.fixture
async def app_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    app_engine = create_async_engine(database_url, connect_args={"server_settings": {"role": "app_user"}})
    yield async_sessionmaker(app_engine, expire_on_commit=False)
    await app_engine.dispose()


@pytest_asyncio.fixture
async def world(seed_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    async with seed_factory() as s:
        n = 600 + (uuid.uuid4().int % 300)
        org = Org(name=f"DV-{uuid.uuid4().hex[:8]}", permission_number=n)
        s.add(org)
        await s.flush()
        suffix = uuid.uuid4().hex[:8]
        profile = UserProfile(auth_subject=f"s-{suffix}", username=f"m-{suffix}", email=f"m-{suffix}@example.test")
        s.add(profile)
        await s.flush()
        membership = UserOrgMembership(profile_id=profile.id, org_id=org.id, is_org_admin=False)
        membership.regions, membership.departments, membership.roles, membership.groups = [], [], [], []
        s.add(membership)
        hr_mask = encode(org=n, region=MAX_REGION, dept=5, role=MAX_ROLE, group=MAX_GROUP)
        public = Folder(name="Public", org_id=org.id, dot_path="Public")
        hr = Folder(
            name="HR", org_id=org.id, dot_path="HR", viewer_permissions_config=[{}], view_permission_masks=[hr_mask]
        )
        s.add_all([public, hr])
        await s.flush()
        docs = {
            "public": Document(
                title="public", org_id=org.id, folder_id=public.id, text="p", document_key=f"p-{suffix}"
            ),
            "hr": Document(title="hr", org_id=org.id, folder_id=hr.id, text="h", document_key=f"h-{suffix}"),
            "override": Document(
                title="override",
                org_id=org.id,
                folder_id=public.id,
                text="o",
                document_key=f"o-{suffix}",
                viewer_permissions_config=[{}],
                view_permission_masks=[hr_mask],
            ),
        }
        docs["own_unfiled"] = Document(
            title="own_unfiled",
            org_id=org.id,
            folder_id=None,
            text="u",
            uploaded_by_id=profile.id,
            document_key=f"u-{suffix}",
        )
        docs["other_unfiled"] = Document(
            title="other_unfiled", org_id=org.id, folder_id=None, text="v", document_key=f"v-{suffix}"
        )
        s.add_all(docs.values())
        await s.commit()
    return {"org": org, "profile": profile, "membership_id": membership.id, "docs": docs}


async def _client(app_factory: Any, world: dict[str, Any], *, admin: bool) -> AsyncGenerator[httpx.AsyncClient]:
    org: Org = world["org"]

    async def ctx() -> OrgContext:
        async with app_factory() as s:
            await db_scope.enter_tenant(s, org.id)
            membership = (
                await s.execute(
                    select(UserOrgMembership)
                    .where(UserOrgMembership.id == world["membership_id"])
                    .options(
                        selectinload(UserOrgMembership.regions),
                        selectinload(UserOrgMembership.departments),
                        selectinload(UserOrgMembership.roles),
                        selectinload(UserOrgMembership.groups),
                    )
                )
            ).scalar_one()
        user = CurrentUser(
            sub="s", username="m", email="m@example.test", profile_id=world["profile"].id, is_site_admin=False
        )
        return OrgContext(user=user, org_id=org.id, membership=membership, is_org_admin=admin)

    async def tenant_db() -> AsyncGenerator[AsyncSession]:
        async with app_factory() as s:
            await db_scope.enter_tenant(s, org.id)
            yield s
            await s.commit()

    redis = MagicMock()
    redis.lrange = AsyncMock(return_value=[])
    brain = MagicMock()
    brain.get_document_chunks = AsyncMock(return_value={"chunks": []})
    brain.get_document_summary = AsyncMock(return_value={"summary": ""})
    app = FastAPI()
    app.include_router(documents_router.router, prefix="/api/documents")
    app.dependency_overrides[require_org_access] = ctx
    app.dependency_overrides[get_tenant_db] = tenant_db
    app.dependency_overrides[get_redis] = lambda: redis
    with patch.object(documents_router, "BrainAPIClient", lambda _s: brain):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield http


@pytest_asyncio.fixture
async def member(app_factory: Any, world: dict[str, Any]) -> AsyncGenerator[httpx.AsyncClient]:
    async for http in _client(app_factory, world, admin=False):
        yield http


@pytest_asyncio.fixture
async def admin(app_factory: Any, world: dict[str, Any]) -> AsyncGenerator[httpx.AsyncClient]:
    async for http in _client(app_factory, world, admin=True):
        yield http


@pytest.mark.parametrize("suffix", _SUFFIXES)
async def test_member_cannot_read_hidden_documents_by_id(
    world: dict[str, Any], member: httpx.AsyncClient, suffix: str
) -> None:
    for name in ("hr", "override"):
        resp = await member.get(f"/api/documents/{world['docs'][name].id}{suffix}")
        assert resp.status_code == 404, f"{name}{suffix}: {resp.status_code}"


@pytest.mark.parametrize("suffix", _SUFFIXES)
async def test_member_reads_visible_documents(world: dict[str, Any], member: httpx.AsyncClient, suffix: str) -> None:
    resp = await member.get(f"/api/documents/{world['docs']['public'].id}{suffix}")
    assert resp.status_code == 200, resp.text


async def test_member_cannot_resolve_a_hidden_document_by_key(world: dict[str, Any], member: httpx.AsyncClient) -> None:
    resp = await member.get(f"/api/documents/by-key/{world['docs']['hr'].document_key}")
    assert resp.status_code == 404


@pytest.mark.parametrize("suffix", _SUFFIXES)
async def test_admin_reads_everything(world: dict[str, Any], admin: httpx.AsyncClient, suffix: str) -> None:
    for name in ("public", "hr", "override"):
        resp = await admin.get(f"/api/documents/{world['docs'][name].id}{suffix}")
        assert resp.status_code == 200, f"{name}{suffix}: {resp.text}"


async def test_member_reads_their_own_unfiled_upload(world: dict[str, Any], member: httpx.AsyncClient) -> None:
    """Unfiled documents bypass folder permissions, so members can't see each
    other's — but the person who uploaded one can still open it."""
    resp = await member.get(f"/api/documents/{world['docs']['own_unfiled'].id}")
    assert resp.status_code == 200, resp.text
    resp = await member.get(f"/api/documents/{world['docs']['other_unfiled'].id}")
    assert resp.status_code == 404


async def test_member_list_applies_per_document_overrides(world: dict[str, Any], member: httpx.AsyncClient) -> None:
    """A document tightened by its own viewer override is hidden from the member list
    even though its folder is visible."""
    resp = await member.get("/api/documents/?page_size=200")
    assert resp.status_code == 200, resp.text
    titles = {d["title"] for d in resp.json()["items"]}
    assert "public" in titles
    assert "override" not in titles
    assert "hr" not in titles


async def test_admin_list_still_sees_overrides(world: dict[str, Any], admin: httpx.AsyncClient) -> None:
    resp = await admin.get("/api/documents/?page_size=200")
    assert {"public", "override", "hr"} <= {d["title"] for d in resp.json()["items"]}
