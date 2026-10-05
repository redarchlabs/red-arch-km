"""API keys bound directly to dimension assignments (and narrowed by folders), end to
end on real PostgreSQL.

These drive the assembled ``/api/v1`` router through the REAL key-auth path (hash
lookup on a bypass session, scope resolution, RLS tenant session), so they pin the
whole chain rather than one function in it:

* A key holding only a role reads with exactly the masks of a member holding only
  that role — search, chat, folders, documents, chunks.
* Folders only narrow: the listed folders plus their subfolders, intersected with
  what the masks may see. A folder outside the list is a 404. A key whose allowed
  folders are all hidden gets an empty answer and brain-api is never called (an
  empty ``folder_tags`` list would mean "no folder filter" there).
* A scoped key with no assignment rows is refused, never widened to org-wide.
* An org key keeps today's org-wide behaviour.
* Deleting a role/folder an active key holds is a 409; revoked keys' rows are
  purged first. Deleting the whole org still cascades.

brain-api is replaced by a recorder: what these tests assert is exactly what the API
hands brain-api, because brain-api filters on precisely that.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from access_mask import MAX_DEPT, MAX_GROUP, MAX_REGION, encode
from api import db_scope
from api.auth import api_key as ak
from api.dependencies import get_db, get_redis
from api.models.api_key import ApiKey, api_key_folders, api_key_roles
from api.models.document import Document, Folder
from api.models.org import Org, Role
from api.models.user import UserOrgMembership, UserProfile
from api.repositories.org import OrgRepository
from api.routers import dimensions as dimensions_router
from api.routers import folders as folders_router
from api.routers import v1 as v1_router
from api.routers.v1 import knowledge as v1_knowledge
from api.routers.v1 import search as v1_search
from api.services.api_key_assignments import KeyAssignments
from api.services.api_key_service import ApiKeyService, ApiKeyValidationError, generate_key
from api.services.api_rate_limit import RateLimitResult
from api.services.search_access import resolve_profile_access_keys
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_ALLOWED = RateLimitResult(allowed=True, limit=600, remaining=599, retry_after=0)
_SCOPES = ["search:read", "knowledge:read", "knowledge:write"]


@dataclass
class World:
    org: Org
    role_a: Role
    role_b: Role
    member_a: UserProfile  # a plain member holding only role A
    public: Folder
    a_only: Folder
    b_only: Folder
    projects: Folder
    projects_sub: Folder
    docs: dict[str, Document]
    org_key: str
    role_key: str  # roles=[A]
    projects_key: str  # roles=[A], folders=[Projects]
    folders_only_key: str  # folders=[Public]
    hidden_key: str  # roles=[A], folders=[B-only] (B-only became hidden after mint)
    empty_key: str  # scoped, no rows
    key_ids: dict[str, uuid.UUID]


@pytest_asyncio.fixture
async def seed_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """Superuser sessions for seeding and inspecting rows (bypass RLS)."""
    seed_engine = create_async_engine(database_url)
    yield async_sessionmaker(seed_engine, expire_on_commit=False)
    await seed_engine.dispose()


@pytest_asyncio.fixture
async def app_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """The app's own sessions, as the non-superuser ``app_user`` (RLS enforced)."""
    app_engine = create_async_engine(database_url, connect_args={"server_settings": {"role": "app_user"}})
    yield async_sessionmaker(app_engine, expire_on_commit=False)
    await app_engine.dispose()


async def _folder(
    s: AsyncSession, org: Org, path: str, masks: list[int] | None, parent: Folder | None = None
) -> Folder:
    folder = Folder(
        name=path.rsplit(".", 1)[-1],
        org_id=org.id,
        dot_path=path,
        parent_id=parent.id if parent else None,
        viewer_permissions_config=[{"note": "test"}] if masks is not None else None,
        view_permission_masks=masks or [],
    )
    s.add(folder)
    await s.flush()
    return folder


async def _doc(s: AsyncSession, org: Org, title: str, folder: Folder | None) -> Document:
    doc = Document(
        title=title,
        org_id=org.id,
        folder_id=folder.id if folder else None,
        text=f"{title} body",
        processing_status="SUCCESS",
    )
    s.add(doc)
    await s.flush()
    return doc


async def _key(
    s: AsyncSession,
    org: Org,
    *,
    scoped: bool = False,
    roles: tuple[Role, ...] = (),
    folders: tuple[Folder, ...] = (),
    scopes: list[str] | None = None,
) -> tuple[str, uuid.UUID]:
    """Insert a key and its assignment rows directly (minting is tested separately)."""
    generated = generate_key()
    key = ApiKey(
        name=f"k-{uuid.uuid4().hex[:6]}",
        key_prefix=generated.prefix,
        key_hash=generated.key_hash,
        scopes=scopes or _SCOPES,
        org_id=org.id,
        access_mode="scoped" if scoped or roles or folders else "org",
    )
    s.add(key)
    await s.flush()
    if roles:
        await s.execute(api_key_roles.insert(), [{"api_key_id": key.id, "role_id": r.id} for r in roles])
    if folders:
        await s.execute(api_key_folders.insert(), [{"api_key_id": key.id, "folder_id": f.id} for f in folders])
    return generated.plaintext, key.id


def _role_mask(org_number: int, role: Role) -> int:
    return encode(org=org_number, region=MAX_REGION, dept=MAX_DEPT, role=role.permission_number, group=MAX_GROUP)


@pytest_asyncio.fixture
async def world(seed_factory: async_sessionmaker[AsyncSession]) -> World:
    n = 300 + (uuid.uuid4().int % 500)
    async with seed_factory() as s:
        org = Org(name=f"DK-{uuid.uuid4().hex[:8]}", permission_number=n)
        s.add(org)
        await s.flush()
        role_a = Role(name="Analyst", org_id=org.id, permission_number=1)
        role_b = Role(name="Buyer", org_id=org.id, permission_number=2)
        s.add_all([role_a, role_b])
        await s.flush()

        tag = uuid.uuid4().hex[:8]
        member_a = UserProfile(auth_subject=f"s-{tag}", username=f"a-{tag}", email=f"a-{tag}@example.test")
        s.add(member_a)
        await s.flush()
        m = UserOrgMembership(profile_id=member_a.id, org_id=org.id, is_org_admin=False)
        m.regions, m.departments, m.groups = [], [], []
        m.roles = [role_a]
        s.add(m)
        await s.flush()

        public = await _folder(s, org, "Public", None)
        a_only = await _folder(s, org, "AOnly", [_role_mask(n, role_a)])
        b_only = await _folder(s, org, "BOnly", [_role_mask(n, role_b)])
        projects = await _folder(s, org, "Projects", None)
        projects_sub = await _folder(s, org, "Projects.Sub", None, parent=projects)
        docs = {
            "public": await _doc(s, org, "public-doc", public),
            "a": await _doc(s, org, "a-doc", a_only),
            "b": await _doc(s, org, "b-doc", b_only),
            "projects": await _doc(s, org, "projects-doc", projects),
            "sub": await _doc(s, org, "sub-doc", projects_sub),
            "unfiled": await _doc(s, org, "unfiled-doc", None),
        }

        ids: dict[str, uuid.UUID] = {}
        org_key, ids["org"] = await _key(s, org)
        role_key, ids["role"] = await _key(s, org, roles=(role_a,))
        projects_key, ids["projects"] = await _key(s, org, roles=(role_a,), folders=(projects,))
        folders_only_key, ids["folders_only"] = await _key(s, org, folders=(public,))
        hidden_key, ids["hidden"] = await _key(s, org, roles=(role_a,), folders=(b_only,))
        empty_key, ids["empty"] = await _key(s, org, scoped=True)
        await s.commit()

    return World(
        org=org,
        role_a=role_a,
        role_b=role_b,
        member_a=member_a,
        public=public,
        a_only=a_only,
        b_only=b_only,
        projects=projects,
        projects_sub=projects_sub,
        docs=docs,
        org_key=org_key,
        role_key=role_key,
        projects_key=projects_key,
        folders_only_key=folders_only_key,
        hidden_key=hidden_key,
        empty_key=empty_key,
        key_ids=ids,
    )


class _Brain:
    """Stand-in for BrainAPIClient that records what it was asked for."""

    def __init__(self) -> None:
        self.vector_search = AsyncMock(return_value={"hits": [], "total": 0})
        self.vector_chat = AsyncMock(return_value={"answer": "", "sources": [], "graph_context": []})
        self.get_document_chunks = AsyncMock(return_value={"chunks": [], "total": 0})
        self.get_document_summary = AsyncMock(return_value={"summary": ""})


@pytest_asyncio.fixture
async def client(app_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[tuple[httpx.AsyncClient, _Brain]]:
    app = FastAPI()
    app.include_router(v1_router.router, prefix="/api/v1")
    app.dependency_overrides[get_redis] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    brain = _Brain()
    with (
        patch.object(ak, "get_session_factory", lambda _settings: app_factory),
        patch.object(ak, "check_rate_limit", AsyncMock(return_value=_ALLOWED)),
        patch.object(v1_search, "BrainAPIClient", lambda _settings: brain),
        patch.object(v1_search, "org_default_llm_model", AsyncMock(return_value=None)),
        patch.object(v1_knowledge, "BrainAPIClient", lambda _settings: brain),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield http, brain


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _tags(*folders: Folder) -> list[str]:
    return sorted(f"folder:{f.id}" for f in folders)


class TestMaskEquivalence:
    async def test_role_key_reads_with_exactly_a_role_members_masks(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, brain = client
        async with seed_factory() as s:
            member_masks = await resolve_profile_access_keys(s, world.org.id, world.member_a.id)
        assert member_masks

        resp = await http.post("/api/v1/search", json={"query": "q"}, headers=_auth(world.role_key))
        assert resp.status_code == 200, resp.text
        sent = brain.vector_search.await_args.kwargs["access_keys"]
        assert sorted(sent) == sorted(member_masks)
        assert brain.vector_search.await_args.kwargs["folder_tags"] is None  # no folder limit

        resp = await http.post("/api/v1/search/chat", json={"query": "q"}, headers=_auth(world.role_key))
        assert resp.status_code == 200, resp.text
        assert sorted(brain.vector_chat.await_args.kwargs["access_keys"]) == sorted(member_masks)

    async def test_role_key_sees_the_folders_a_role_member_sees(self, world: World, client: Any) -> None:
        http, _ = client
        resp = await http.get("/api/v1/knowledge/folders", headers=_auth(world.role_key))
        assert resp.status_code == 200
        assert {f["name"] for f in resp.json()} == {"Public", "AOnly", "Projects", "Sub"}

    async def test_org_key_stays_org_wide(self, world: World, client: Any) -> None:
        http, brain = client
        resp = await http.post("/api/v1/search", json={"query": "q"}, headers=_auth(world.org_key))
        assert resp.status_code == 200
        assert brain.vector_search.await_args.kwargs["access_keys"] is None
        assert brain.vector_search.await_args.kwargs["folder_tags"] is None
        resp = await http.get("/api/v1/knowledge/folders", headers=_auth(world.org_key))
        assert {f["name"] for f in resp.json()} == {"Public", "AOnly", "BOnly", "Projects", "Sub"}

    async def test_folders_only_key_gets_no_assignment_masks_never_org_wide(self, world: World, client: Any) -> None:
        http, brain = client
        resp = await http.post("/api/v1/search", json={"query": "q"}, headers=_auth(world.folders_only_key))
        assert resp.status_code == 200, resp.text
        sent = brain.vector_search.await_args.kwargs["access_keys"]
        assert sent is not None and sent
        assert _role_mask(world.org.permission_number, world.role_a) not in sent
        assert brain.vector_search.await_args.kwargs["folder_tags"] == [f"folder:{world.public.id}"]


class TestFolderNarrowing:
    async def test_listed_folder_includes_its_subfolders(self, world: World, client: Any) -> None:
        http, brain = client
        resp = await http.get("/api/v1/knowledge/folders", headers=_auth(world.projects_key))
        assert {f["name"] for f in resp.json()} == {"Projects", "Sub"}

        resp = await http.post("/api/v1/search", json={"query": "q"}, headers=_auth(world.projects_key))
        assert resp.status_code == 200
        assert sorted(brain.vector_search.await_args.kwargs["folder_tags"]) == _tags(world.projects, world.projects_sub)

    async def test_chat_always_sends_the_folder_tags(self, world: World, client: Any) -> None:
        """brain-api applies folder_tags to graph facts too; the knowledge graph stays on."""
        http, brain = client
        resp = await http.post("/api/v1/search/chat", json={"query": "q"}, headers=_auth(world.projects_key))
        assert resp.status_code == 200
        kwargs = brain.vector_chat.await_args.kwargs
        assert sorted(kwargs["folder_tags"]) == _tags(world.projects, world.projects_sub)
        assert kwargs["use_knowledge_graph"] is True

    async def test_requesting_a_subset_is_allowed(self, world: World, client: Any) -> None:
        http, brain = client
        resp = await http.post(
            "/api/v1/search",
            json={"query": "q", "folder_ids": [str(world.projects_sub.id)]},
            headers=_auth(world.projects_key),
        )
        assert resp.status_code == 200
        assert brain.vector_search.await_args.kwargs["folder_tags"] == [f"folder:{world.projects_sub.id}"]

    @pytest.mark.parametrize("path", ["/api/v1/search", "/api/v1/search/chat"])
    async def test_a_folder_outside_the_list_is_404(self, world: World, client: Any, path: str) -> None:
        http, brain = client
        resp = await http.post(
            path, json={"query": "q", "folder_ids": [str(world.public.id)]}, headers=_auth(world.projects_key)
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "folder not found"
        brain.vector_search.assert_not_awaited()
        brain.vector_chat.assert_not_awaited()

    async def test_documents_outside_the_list_are_404(self, world: World, client: Any) -> None:
        http, brain = client
        resp = await http.get(
            f"/api/v1/knowledge/documents?folder_id={world.public.id}", headers=_auth(world.projects_key)
        )
        assert resp.status_code == 404
        for key in ("public", "a", "unfiled"):
            for suffix in ("", "/chunks"):
                resp = await http.get(
                    f"/api/v1/knowledge/documents/{world.docs[key].id}{suffix}", headers=_auth(world.projects_key)
                )
                assert resp.status_code == 404, f"{key}{suffix}"
        brain.get_document_chunks.assert_not_awaited()

    async def test_document_list_is_the_folder_set(self, world: World, client: Any) -> None:
        http, _ = client
        resp = await http.get("/api/v1/knowledge/documents?page_size=200", headers=_auth(world.projects_key))
        assert resp.status_code == 200
        assert {d["title"] for d in resp.json()["items"]} == {"projects-doc", "sub-doc"}
        resp = await http.get(
            f"/api/v1/knowledge/documents/{world.docs['sub'].id}/summary", headers=_auth(world.projects_key)
        )
        assert resp.status_code == 200

    async def test_folders_never_grant_beyond_the_masks(self, world: World, client: Any, seed_factory: Any) -> None:
        """A listed folder that the masks cannot see (its permissions tightened after
        mint) drops out of the set: folders only narrow."""
        http, _ = client
        async with seed_factory() as s:
            await s.execute(
                text(
                    "UPDATE folders SET viewer_permissions_config = '[{}]', view_permission_masks = :m WHERE id = :id"
                ),
                {"m": [_role_mask(world.org.permission_number, world.role_b)], "id": world.projects_sub.id},
            )
            await s.commit()
        resp = await http.get("/api/v1/knowledge/folders", headers=_auth(world.projects_key))
        assert {f["name"] for f in resp.json()} == {"Projects"}


class TestEmptyAllowedSet:
    @pytest.mark.parametrize("path", ["/api/v1/search", "/api/v1/search/chat"])
    async def test_short_circuits_without_calling_brain(self, world: World, client: Any, path: str) -> None:
        http, brain = client
        resp = await http.post(path, json={"query": "q"}, headers=_auth(world.hidden_key))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("hits", []) == [] and body.get("sources", []) == []
        brain.vector_search.assert_not_awaited()
        brain.vector_chat.assert_not_awaited()

    async def test_lists_are_empty(self, world: World, client: Any) -> None:
        http, _ = client
        assert (await http.get("/api/v1/knowledge/folders", headers=_auth(world.hidden_key))).json() == []
        resp = await http.get("/api/v1/knowledge/documents", headers=_auth(world.hidden_key))
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestFailClosed:
    async def test_scoped_key_with_no_rows_is_refused(self, world: World, client: Any) -> None:
        http, brain = client
        for method, path in (("post", "/api/v1/search"), ("get", "/api/v1/knowledge/folders")):
            kwargs: dict[str, Any] = {"json": {"query": "q"}} if method == "post" else {}
            resp = await getattr(http, method)(path, headers=_auth(world.empty_key), **kwargs)
            assert resp.status_code == 403, resp.text
        brain.vector_search.assert_not_awaited()

    async def test_scoped_key_cannot_use_unaware_scopes(self, world: World, client: Any, seed_factory: Any) -> None:
        async with seed_factory() as s:
            await s.execute(
                text("UPDATE api_keys SET scopes = '[\"entities:read\"]' WHERE id = :id"), {"id": world.key_ids["role"]}
            )
            await s.commit()
        http, _ = client
        resp = await http.get("/api/v1/entities", headers=_auth(world.role_key))
        assert resp.status_code == 403


class _Ctx(SimpleNamespace):
    pass


class TestDeletingAnAssignedItem:
    async def _delete_role(self, app_factory: Any, world: World, role: Role) -> None:
        async with app_factory() as s:
            await db_scope.enter_tenant(s, world.org.id)
            await dimensions_router.delete_dimension(
                dimension="roles", dimension_id=role.id, ctx=_Ctx(org_id=world.org.id), session=s
            )
            await s.commit()

    async def _delete_folder(self, app_factory: Any, world: World, folder: Folder) -> None:
        async with app_factory() as s:
            await db_scope.enter_tenant(s, world.org.id)
            await folders_router.delete_folder(folder_id=folder.id, ctx=_Ctx(org_id=world.org.id), session=s)
            await s.commit()

    async def test_role_held_by_an_active_key_is_409_naming_it(self, world: World, app_factory: Any) -> None:
        with pytest.raises(HTTPException) as exc:
            await self._delete_role(app_factory, world, world.role_a)
        assert exc.value.status_code == 409
        assert "API key" in exc.value.detail

    async def test_folder_held_by_an_active_key_is_409(self, world: World, app_factory: Any) -> None:
        with pytest.raises(HTTPException) as exc:
            await self._delete_folder(app_factory, world, world.public)
        assert exc.value.status_code == 409

    async def test_revoked_keys_do_not_block_and_are_purged(
        self, world: World, app_factory: Any, seed_factory: Any
    ) -> None:
        async with seed_factory() as s:
            await s.execute(
                text("UPDATE api_keys SET revoked_at = :t WHERE id = :id"),
                {"t": datetime.now(UTC), "id": world.key_ids["folders_only"]},
            )
            await s.commit()
        await self._delete_folder(app_factory, world, world.public)
        async with seed_factory() as s:
            assert await s.get(Folder, world.public.id) is None
            left = (
                await s.execute(
                    select(func.count())
                    .select_from(api_key_folders)
                    .where(api_key_folders.c.api_key_id == world.key_ids["folders_only"])
                )
            ).scalar_one()
        assert left == 0

    async def test_the_database_refuses_it_too(self, world: World, seed_factory: Any) -> None:
        """The FK is deferred to commit (so org deletion can cascade), but it holds."""
        async with seed_factory() as s:
            await s.execute(text("DELETE FROM roles WHERE id = :id"), {"id": world.role_a.id})
            with pytest.raises(IntegrityError):
                await s.commit()
            await s.rollback()
        async with seed_factory() as s:
            assert await s.get(Role, world.role_a.id) is not None


class TestOrgDeletionStillCascades:
    async def test_one_statement(self, world: World, seed_factory: Any) -> None:
        """The item FK is deferred to commit, by which time the cascade has removed
        the key rows too."""
        async with seed_factory() as s:
            await s.execute(text("DELETE FROM orgs WHERE id = :id"), {"id": world.org.id})
            await s.commit()
            assert await s.get(Org, world.org.id) is None
            assert (await s.execute(select(ApiKey).where(ApiKey.org_id == world.org.id))).first() is None

    async def test_through_the_org_repository(self, world: World, seed_factory: Any) -> None:
        """The app deletes an org through the ORM, which removes its roles one by one
        before the org row (and the org's keys with it)."""
        async with seed_factory() as s:
            assert await OrgRepository(s).delete(world.org.id)
            await s.commit()
            assert await s.get(Org, world.org.id) is None
            assert (
                await s.execute(select(api_key_roles).where(api_key_roles.c.role_id == world.role_a.id))
            ).first() is None


class TestMinting:
    async def _mint(self, app_factory: Any, world: World, **assignments: Any) -> ApiKey:
        async with app_factory() as s:
            await db_scope.enter_tenant(s, world.org.id)
            key, _plain = await ApiKeyService(s, world.org.id).create_key(
                name="k",
                scopes=["search:read"],
                expires_at=None,
                created_by_profile_id=None,
                assignments=KeyAssignments.of(**assignments),
            )
            await s.commit()
            return key

    async def test_assignments_make_the_key_scoped_and_are_stored(
        self, world: World, app_factory: Any, seed_factory: Any
    ) -> None:
        key = await self._mint(app_factory, world, roles=[world.role_a.id], folders=[world.projects.id])
        assert key.access_mode == "scoped"
        async with seed_factory() as s:
            labels = await ApiKeyService(s, world.org.id).assignment_labels([key])
        assert labels[key.id].roles == [(world.role_a.id, "Analyst")]
        assert labels[key.id].folders == [(world.projects.id, "Projects")]

    async def test_no_assignments_is_an_org_key(self, world: World, app_factory: Any) -> None:
        key = await self._mint(app_factory, world)
        assert key.access_mode == "org"

    async def test_a_role_from_another_org_is_refused(self, world: World, app_factory: Any, seed_factory: Any) -> None:
        async with seed_factory() as s:
            other = Org(name=f"DK-other-{uuid.uuid4().hex[:8]}", permission_number=1800 + uuid.uuid4().int % 200)
            s.add(other)
            await s.flush()
            foreign = Role(name="Foreign", org_id=other.id, permission_number=1)
            s.add(foreign)
            await s.commit()
        with pytest.raises(ApiKeyValidationError, match="Unknown role"):
            await self._mint(app_factory, world, roles=[foreign.id])

    async def test_a_folder_the_assignments_cannot_see_is_refused(self, world: World, app_factory: Any) -> None:
        with pytest.raises(ApiKeyValidationError, match="not visible"):
            await self._mint(app_factory, world, roles=[world.role_a.id], folders=[world.b_only.id])

    @pytest.mark.parametrize("scopes", [["records:read"], ["*"], ["knowledge:*"]])
    async def test_scoped_keys_hold_only_scope_aware_scopes(
        self, world: World, app_factory: Any, scopes: list[str]
    ) -> None:
        async with app_factory() as s:
            await db_scope.enter_tenant(s, world.org.id)
            with pytest.raises(ApiKeyValidationError, match="dimension or folder"):
                await ApiKeyService(s, world.org.id).create_key(
                    name="k",
                    scopes=scopes,
                    expires_at=None,
                    created_by_profile_id=None,
                    assignments=KeyAssignments.of(roles=[world.role_a.id]),
                )
