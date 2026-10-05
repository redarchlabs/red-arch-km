"""Partially-scoped folders are visible to the members they name (real PostgreSQL).

A folder configured as ``{"department": "Finance"}`` gets a mask whose other
dimensions are wildcards. Visibility is decided by exact integer overlap, so this
only works because member masks are expanded with wildcard variants
(``access_mask.expand_member_masks``, applied in
``calculate_user_masks_from_membership``). Before that, such a folder was visible
to no member at all.

Checked through every path that resolves masks: a member's own session masks, a
profile (agents), and a key bound to the same dimension assignments (``/api/v1``).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from api.auth import api_key as ak
from api.dependencies import get_db, get_redis
from api.models.api_key import ApiKey, api_key_departments, api_key_regions
from api.models.document import Folder
from api.models.org import Department, Org, Region
from api.models.user import UserOrgMembership, UserProfile
from api.repositories.folder import FolderRepository
from api.routers import v1 as v1_router
from api.services.api_key_service import generate_key
from api.services.api_rate_limit import RateLimitResult
from api.services.folder_service import compute_folder_masks
from api.services.search_access import resolve_profile_access_keys
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_ALLOWED = RateLimitResult(allowed=True, limit=600, remaining=599, retry_after=0)


@dataclass
class World:
    org: Org
    finance_west: UserProfile
    finance_east: UserProfile
    hr_west: UserProfile
    finance_folder: Folder
    finance_west_folder: Folder
    keys: dict[str, str]


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


async def _member(s: AsyncSession, org: Org, region: Region, dept: Department) -> UserProfile:
    suffix = uuid.uuid4().hex[:8]
    profile = UserProfile(auth_subject=f"sub-{suffix}", username=f"m-{suffix}", email=f"m-{suffix}@example.test")
    s.add(profile)
    await s.flush()
    membership = UserOrgMembership(profile_id=profile.id, org_id=org.id, is_org_admin=False)
    membership.regions, membership.departments, membership.roles, membership.groups = [region], [dept], [], []
    s.add(membership)
    await s.flush()
    return profile


async def _folder(s: AsyncSession, org: Org, name: str, config: list[dict[str, str]]) -> Folder:
    view, contrib = await compute_folder_masks(s, org.id, config, None)
    folder = Folder(
        name=name,
        org_id=org.id,
        dot_path=name,
        viewer_permissions_config=config,
        view_permission_masks=view,
        contributor_permission_masks=contrib,
    )
    s.add(folder)
    await s.flush()
    return folder


@pytest_asyncio.fixture
async def world(seed_factory: async_sessionmaker[AsyncSession]) -> World:
    async with seed_factory() as s:
        org = Org(name=f"WC-{uuid.uuid4().hex[:8]}", permission_number=200 + (uuid.uuid4().int % 300))
        s.add(org)
        await s.flush()
        west = Region(name="West", permission_number=3, org_id=org.id)
        east = Region(name="East", permission_number=4, org_id=org.id)
        finance = Department(name="Finance", permission_number=5, org_id=org.id)
        hr = Department(name="HR", permission_number=6, org_id=org.id)
        s.add_all([west, east, finance, hr])
        await s.flush()
        finance_west = await _member(s, org, west, finance)
        finance_east = await _member(s, org, east, finance)
        hr_west = await _member(s, org, west, hr)
        finance_folder = await _folder(s, org, "Finance", [{"department": "Finance"}])
        finance_west_folder = await _folder(s, org, "FinanceWest", [{"region": "West", "department": "Finance"}])
        keys: dict[str, str] = {}
        # Each key holds exactly its member's assignments.
        for label, region, dept in (
            ("finance_west", west, finance),
            ("finance_east", east, finance),
            ("hr_west", west, hr),
        ):
            generated = generate_key()
            key = ApiKey(
                name=label,
                key_prefix=generated.prefix,
                key_hash=generated.key_hash,
                scopes=["knowledge:read", "search:read"],
                org_id=org.id,
                access_mode="scoped",
            )
            s.add(key)
            await s.flush()
            await s.execute(api_key_regions.insert().values(api_key_id=key.id, region_id=region.id))
            await s.execute(api_key_departments.insert().values(api_key_id=key.id, department_id=dept.id))
            keys[label] = generated.plaintext
        await s.commit()
    return World(org, finance_west, finance_east, hr_west, finance_folder, finance_west_folder, keys)


async def _visible(seed_factory: Any, world: World, profile: UserProfile) -> set[str]:
    async with seed_factory() as s:
        masks = await resolve_profile_access_keys(s, world.org.id, profile.id)
        assert masks  # a plain member: restricted, never None
        folders, _ = await FolderRepository(s, world.org.id).list_visible_to_masks(user_masks=masks)
    return {f.name for f in folders}


class TestProfileMasks:
    async def test_department_only_folder_is_visible_to_that_department(self, world: World, seed_factory: Any) -> None:
        assert "Finance" in await _visible(seed_factory, world, world.finance_west)
        assert "Finance" in await _visible(seed_factory, world, world.finance_east)

    async def test_department_only_folder_is_hidden_from_other_departments(
        self, world: World, seed_factory: Any
    ) -> None:
        assert "Finance" not in await _visible(seed_factory, world, world.hr_west)

    async def test_region_and_department_folder_needs_both(self, world: World, seed_factory: Any) -> None:
        assert "FinanceWest" in await _visible(seed_factory, world, world.finance_west)
        assert "FinanceWest" not in await _visible(seed_factory, world, world.finance_east)  # wrong region
        assert "FinanceWest" not in await _visible(seed_factory, world, world.hr_west)  # wrong department


class TestDimensionBoundKeys:
    @pytest_asyncio.fixture
    async def http(self, app_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[httpx.AsyncClient]:
        app = FastAPI()
        app.include_router(v1_router.router, prefix="/api/v1")
        app.dependency_overrides[get_redis] = lambda: MagicMock()
        app.dependency_overrides[get_db] = lambda: MagicMock()
        with (
            patch.object(ak, "get_session_factory", lambda _s: app_factory),
            patch.object(ak, "check_rate_limit", AsyncMock(return_value=_ALLOWED)),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    async def _folders(self, http: httpx.AsyncClient, key: str) -> set[str]:
        resp = await http.get("/api/v1/knowledge/folders", headers={"Authorization": f"Bearer {key}"})
        assert resp.status_code == 200, resp.text
        return {f["name"] for f in resp.json()}

    async def test_scoped_keys_see_partially_scoped_folders_they_match(
        self, world: World, http: httpx.AsyncClient
    ) -> None:
        assert await self._folders(http, world.keys["finance_west"]) == {"Finance", "FinanceWest"}
        assert await self._folders(http, world.keys["finance_east"]) == {"Finance"}
        assert await self._folders(http, world.keys["hr_west"]) == set()
