"""A scoped key's folder set and the lifecycle of what it holds, on real PostgreSQL.

* **Folders expand by the tree, never by name.** ``dot_path`` is built from folder
  names and two ROOT folders may share a name (the unique constraint treats NULL
  ``parent_id`` values as distinct), so a prefix match on ``dot_path`` would let a
  new root "Finance" — and everything under it — join a key that lists the other
  "Finance". Expansion walks ``parent_id`` from the listed ids instead.
* **Mint refuses a folder set wider than search can carry** (``MAX_FOLDER_TAGS``).
* **Deletes and mints cannot race past each other.** The item FKs are deferred to
  commit, so the handlers lock the rows they depend on and check the constraints
  in-handler: a violation is a 409 there, never a 500 (or a 204 that rolls back)
  after the response.
* **Purging revoked/expired keys' rows is an explicit delete step**, not a side
  effect of asking which active keys hold an item.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from access_mask import MAX_DEPT, MAX_GROUP, MAX_REGION, encode
from api import db_scope
from api.models.api_key import ApiKey, api_key_folders, api_key_roles
from api.models.document import Folder
from api.models.org import Org, Role
from api.routers import dimensions as dimensions_router
from api.routers import folders as folders_router
from api.services import api_key_assignments
from api.services.api_key_assignments import (
    KeyAssignments,
    active_keys_holding,
    purge_inactive_holders,
)
from api.services.api_key_scope import resolve_key_scope
from api.services.api_key_service import (
    ApiKeyAssignmentInvalid,
    ApiKeyConflictError,
    ApiKeyService,
    generate_key,
)
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


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


async def _org(s: AsyncSession) -> Org:
    org = Org(name=f"FS-{uuid.uuid4().hex[:8]}", permission_number=300 + uuid.uuid4().int % 1500)
    s.add(org)
    await s.flush()
    return org


async def _folder(
    s: AsyncSession, org: Org, name: str, parent: Folder | None = None, masks: list[int] | None = None
) -> Folder:
    folder = Folder(
        name=name,
        org_id=org.id,
        dot_path=f"{parent.dot_path}.{name}" if parent else name,
        parent_id=parent.id if parent else None,
        viewer_permissions_config=[{"note": "test"}] if masks is not None else None,
        view_permission_masks=masks or [],
    )
    s.add(folder)
    await s.flush()
    return folder


async def _scoped_key(
    s: AsyncSession, org: Org, *, folders: tuple[Folder, ...] = (), roles: tuple[Role, ...] = ()
) -> ApiKey:
    generated = generate_key()
    key = ApiKey(
        name=f"k-{uuid.uuid4().hex[:6]}",
        key_prefix=generated.prefix,
        key_hash=generated.key_hash,
        scopes=["search:read"],
        org_id=org.id,
        access_mode="scoped",
    )
    s.add(key)
    await s.flush()
    if folders:
        await s.execute(api_key_folders.insert(), [{"api_key_id": key.id, "folder_id": f.id} for f in folders])
    if roles:
        await s.execute(api_key_roles.insert(), [{"api_key_id": key.id, "role_id": r.id} for r in roles])
    return key


def _role_mask(org: Org, role: Role) -> int:
    return encode(
        org=org.permission_number, region=MAX_REGION, dept=MAX_DEPT, role=role.permission_number, group=MAX_GROUP
    )


class TestExpansionFollowsTheTree:
    async def test_a_second_root_with_the_same_name_does_not_join(self, seed_factory: Any) -> None:
        async with seed_factory() as s:
            org = await _org(s)
            finance = await _folder(s, org, "Finance")
            reports = await _folder(s, org, "Reports", parent=finance)
            # Same name, also a root: allowed (NULL parent_id is never "equal").
            impostor = await _folder(s, org, "Finance")
            payroll = await _folder(s, org, "Payroll", parent=impostor)
            assert impostor.dot_path == finance.dot_path
            key = await _scoped_key(s, org, folders=(finance,))
            await s.commit()

            scope = await resolve_key_scope(s, key)

        assert scope.folder_ids == frozenset({finance.id, reports.id})
        assert impostor.id not in scope.folder_ids
        assert payroll.id not in scope.folder_ids

    async def test_deep_subfolders_and_inherited_restrictions(self, seed_factory: Any) -> None:
        """Visibility is still the effective (inherited) one: a subtree under a
        restricted folder the key's masks cannot see drops out, wherever it sits."""
        async with seed_factory() as s:
            org = await _org(s)
            role = Role(name="Buyer", org_id=org.id, permission_number=2)
            s.add(role)
            await s.flush()
            top = await _folder(s, org, "Top")
            mid = await _folder(s, org, "Mid", parent=top)
            leaf = await _folder(s, org, "Leaf", parent=mid)
            locked = await _folder(s, org, "Locked", parent=top, masks=[_role_mask(org, role)])
            under_locked = await _folder(s, org, "UnderLocked", parent=locked)
            key = await _scoped_key(s, org, folders=(top,))
            await s.commit()

            scope = await resolve_key_scope(s, key)

        assert scope.folder_ids == frozenset({top.id, mid.id, leaf.id})
        assert locked.id not in scope.folder_ids
        assert under_locked.id not in scope.folder_ids

    async def test_a_restriction_above_the_listed_folder_still_applies(self, seed_factory: Any) -> None:
        async with seed_factory() as s:
            org = await _org(s)
            role = Role(name="Buyer", org_id=org.id, permission_number=2)
            s.add(role)
            await s.flush()
            locked = await _folder(s, org, "Locked", masks=[_role_mask(org, role)])
            inner = await _folder(s, org, "Inner", parent=locked)
            key = await _scoped_key(s, org, folders=(inner,))
            await s.commit()

            scope = await resolve_key_scope(s, key)

        assert scope.folder_ids == frozenset()


class TestMintCapsTheExpandedSet:
    async def test_more_than_the_search_cap_is_refused_with_paths(
        self, seed_factory: Any, app_factory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api_key_assignments, "MAX_FOLDER_TAGS", 2)
        async with seed_factory() as s:
            org = await _org(s)
            root = await _folder(s, org, "Root")
            await _folder(s, org, "A", parent=root)
            await _folder(s, org, "B", parent=root)
            await s.commit()
        async with app_factory() as s:
            await db_scope.enter_tenant(s, org.id)
            with pytest.raises(ApiKeyAssignmentInvalid, match=r"3 folders.*limit is 2"):
                await ApiKeyService(s, org.id).create_key(
                    name="k",
                    scopes=["search:read"],
                    expires_at=None,
                    created_by_profile_id=None,
                    assignments=KeyAssignments.of(folders=[root.id]),
                )

    async def test_hidden_folders_are_named_by_path(self, seed_factory: Any, app_factory: Any) -> None:
        async with seed_factory() as s:
            org = await _org(s)
            role = Role(name="Buyer", org_id=org.id, permission_number=2)
            s.add(role)
            await s.flush()
            locked = await _folder(s, org, "Locked", masks=[_role_mask(org, role)])
            await s.commit()
        async with app_factory() as s:
            await db_scope.enter_tenant(s, org.id)
            with pytest.raises(ApiKeyAssignmentInvalid, match="Locked") as exc:
                await ApiKeyService(s, org.id).create_key(
                    name="k",
                    scopes=["search:read"],
                    expires_at=None,
                    created_by_profile_id=None,
                    assignments=KeyAssignments.of(folders=[locked.id]),
                )
        assert str(locked.id) not in str(exc.value)


class _Ctx(SimpleNamespace):
    pass


class TestDeleteAndMintCannotRace:
    async def test_a_delete_waits_for_an_open_mint_then_refuses(self, seed_factory: Any, app_factory: Any) -> None:
        """While a mint is open, a delete of the role it validated waits on the
        mint's FOR SHARE lock — then sees the new key and answers 409 instead of
        deleting the role out from under it."""
        async with seed_factory() as s:
            org = await _org(s)
            role = Role(name="Analyst", org_id=org.id, permission_number=1)
            s.add(role)
            await s.commit()

        async with app_factory() as minting, app_factory() as deleting:
            await db_scope.enter_tenant(minting, org.id)
            await ApiKeyService(minting, org.id).create_key(
                name="k",
                scopes=["search:read"],
                expires_at=None,
                created_by_profile_id=None,
                assignments=KeyAssignments.of(roles=[role.id]),
            )
            await db_scope.enter_tenant(deleting, org.id)
            delete = asyncio.create_task(
                dimensions_router.delete_dimension(
                    dimension="roles", dimension_id=role.id, ctx=_Ctx(org_id=org.id), session=deleting
                )
            )
            await asyncio.sleep(0.5)
            assert not delete.done(), "the delete should be waiting on the mint's lock"
            await minting.commit()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(delete, timeout=10)
            assert exc.value.status_code == 409
            assert "API key" in exc.value.detail
            await deleting.rollback()

        async with seed_factory() as s:
            assert await s.get(Role, role.id) is not None

    async def test_a_mint_waits_for_an_open_delete_then_refuses(self, seed_factory: Any, app_factory: Any) -> None:
        """The other order: a mint validating a role that is being deleted waits for
        the delete, then finds the role gone and refuses — no key is created."""
        async with seed_factory() as s:
            org = await _org(s)
            role = Role(name="Analyst", org_id=org.id, permission_number=1)
            s.add(role)
            await s.commit()

        async with app_factory() as deleting, app_factory() as minting:
            await db_scope.enter_tenant(deleting, org.id)
            await dimensions_router.delete_dimension(
                dimension="roles", dimension_id=role.id, ctx=_Ctx(org_id=org.id), session=deleting
            )
            await db_scope.enter_tenant(minting, org.id)
            mint = asyncio.create_task(
                ApiKeyService(minting, org.id).create_key(
                    name="k",
                    scopes=["search:read"],
                    expires_at=None,
                    created_by_profile_id=None,
                    assignments=KeyAssignments.of(roles=[role.id]),
                )
            )
            await asyncio.sleep(0.5)
            assert not mint.done(), "the mint should be waiting on the delete's lock"
            await deleting.commit()
            with pytest.raises(ApiKeyAssignmentInvalid, match="Unknown role"):
                await asyncio.wait_for(mint, timeout=10)
            await minting.rollback()

    async def test_a_violation_at_delete_is_409_in_the_handler(self, seed_factory: Any, app_factory: Any) -> None:
        """If the active-keys check is ever bypassed, the in-handler constraint check
        still turns the FK violation into a 409 rather than a failed commit later."""
        async with seed_factory() as s:
            org = await _org(s)
            folder = await _folder(s, org, "Held")
            await _scoped_key(s, org, folders=(folder,))
            await s.commit()

        async def _nobody(*_a: Any, **_k: Any) -> list[str]:
            return []

        async with app_factory() as s:
            await db_scope.enter_tenant(s, org.id)
            with patch.object(folders_router, "active_keys_holding", _nobody), pytest.raises(HTTPException) as exc:
                await folders_router.delete_folder(folder_id=folder.id, ctx=_Ctx(org_id=org.id), session=s)
            assert exc.value.status_code == 409
            await s.rollback()
        async with seed_factory() as s:
            assert await s.get(Folder, folder.id) is not None

    async def test_a_violation_at_mint_is_a_conflict_not_a_plaintext_key(
        self, seed_factory: Any, app_factory: Any
    ) -> None:
        """A row that vanished between validation and insert is caught in-handler."""
        async with seed_factory() as s:
            org = await _org(s)
            role = Role(name="Analyst", org_id=org.id, permission_number=1)
            s.add(role)
            await s.commit()

        async def _skip(*_a: Any, **_k: Any) -> None:
            return None

        async with app_factory() as s:
            await db_scope.enter_tenant(s, org.id)
            with (
                patch("api.services.api_key_service.validate_assignments", _skip),
                pytest.raises(ApiKeyConflictError),
            ):
                await ApiKeyService(s, org.id).create_key(
                    name="k",
                    scopes=["search:read"],
                    expires_at=None,
                    created_by_profile_id=None,
                    assignments=KeyAssignments.of(roles=[uuid.uuid4()]),
                )
            await s.rollback()


class TestPurgeIsAnExplicitStep:
    async def test_asking_who_holds_an_item_changes_nothing(self, seed_factory: Any) -> None:
        async with seed_factory() as s:
            org = await _org(s)
            folder = await _folder(s, org, "Held")
            key = await _scoped_key(s, org, folders=(folder,))
            key.revoked_at = datetime.now(UTC)
            await s.commit()

            assert await active_keys_holding(s, org.id, "folders", folder.id) == []
            rows = (
                await s.execute(
                    select(func.count()).select_from(api_key_folders).where(api_key_folders.c.api_key_id == key.id)
                )
            ).scalar_one()
            assert rows == 1

            assert await purge_inactive_holders(s, org.id, "folders", folder.id) == 1
            rows = (
                await s.execute(
                    select(func.count()).select_from(api_key_folders).where(api_key_folders.c.api_key_id == key.id)
                )
            ).scalar_one()
            assert rows == 0
