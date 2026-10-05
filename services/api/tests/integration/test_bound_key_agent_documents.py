"""Agent tools in a run started by a scoped API key (real PostgreSQL).

A scoped key's run has no actor. Every tool call reloads the key and its
assignments, so:

* ``create_document`` needs the key to still hold ``knowledge:write`` (org keys
  too), counts against the key's daily document-write cap (the same counter as
  ``POST /api/v1/knowledge/documents``), and for a scoped key a folder inside the
  key's folder set that its masks may add to, never an unfiled document; the
  document has no uploader.
* ``attach_document`` (a work-order deliverable) writes to the knowledge base the
  same way, so it is held to exactly the same rules.
* ``search_knowledge`` reads with the key's masks and folders — even when the agent
  holds ``knowledge_scope: "org"``, which would make an actor-less run
  unrestricted — and refuses once the key is revoked.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from access_mask import MAX_DEPT, MAX_GROUP, MAX_REGION, MAX_ROLE, encode
from api import db_scope
from api.models.api_key import ApiKey, api_key_folders, api_key_roles
from api.models.document import Document, Folder
from api.models.org import Org, Role
from api.services.agents.tools import artifacts as artifact_tools
from api.services.agents.tools import documents as doc_tools
from api.services.agents.tools import knowledge as knowledge_tools
from api.services.agents.tools.spec import ToolContext
from api.services.agents.work_order_service import WorkOrderService
from api.services.api_key_service import generate_key
from api.services.api_rate_limit import RateLimitResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def seed_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    e = create_async_engine(database_url)
    yield async_sessionmaker(e, expire_on_commit=False)
    await e.dispose()


@pytest_asyncio.fixture
async def app_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    e = create_async_engine(database_url, connect_args={"server_settings": {"role": "app_user"}})
    yield async_sessionmaker(e, expire_on_commit=False)
    await e.dispose()


async def _key(
    s: AsyncSession, org: Org, label: str, scopes: list[str], *, role: Role | None, folders: tuple[Folder, ...] = ()
) -> uuid.UUID:
    g = generate_key()
    k = ApiKey(
        name=label,
        key_prefix=g.prefix,
        key_hash=g.key_hash,
        scopes=scopes,
        org_id=org.id,
        access_mode="scoped" if role or folders else "org",
    )
    s.add(k)
    await s.flush()
    if role is not None:
        await s.execute(api_key_roles.insert().values(api_key_id=k.id, role_id=role.id))
    for f in folders:
        await s.execute(api_key_folders.insert().values(api_key_id=k.id, folder_id=f.id))
    return k.id


@pytest_asyncio.fixture
async def world(seed_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    async with seed_factory() as s:
        n = 900 + (uuid.uuid4().int % 300)
        org = Org(name=f"BKD-{uuid.uuid4().hex[:8]}", permission_number=n)
        s.add(org)
        await s.flush()
        role = Role(name="Agent writer", org_id=org.id, permission_number=4)
        s.add(role)
        await s.flush()
        hr = encode(org=n, region=MAX_REGION, dept=5, role=MAX_ROLE, group=MAX_GROUP)
        role_mask = encode(org=n, region=MAX_REGION, dept=MAX_DEPT, role=4, group=MAX_GROUP)
        public = Folder(name="Public", org_id=org.id, dot_path="Public")
        mine = Folder(
            name="Mine",
            org_id=org.id,
            dot_path="Mine",
            viewer_permissions_config=[{}],
            view_permission_masks=[role_mask],
        )
        hidden = Folder(
            name="HR", org_id=org.id, dot_path="HR", viewer_permissions_config=[{}], view_permission_masks=[hr]
        )
        readonly = Folder(
            name="Archive",
            org_id=org.id,
            dot_path="Archive",
            contributor_permissions_config=[{}],
            contributor_permission_masks=[hr],
        )
        s.add_all([public, mine, hidden, readonly])
        await s.flush()
        keys = {
            "writer": await _key(s, org, "writer", ["knowledge:write", "agents:run"], role=role),
            "reader": await _key(s, org, "reader", ["agents:run"], role=role),
            "mine_only": await _key(s, org, "mine", ["knowledge:write", "agents:run"], role=role, folders=(mine,)),
            "org": await _key(s, org, "org", ["agents:run"], role=None),
            "org_writer": await _key(s, org, "org-writer", ["knowledge:write", "agents:run"], role=None),
        }
        await s.commit()
        await db_scope.enter_tenant(s, org.id)
        work_order = await WorkOrderService(s, org.id).create_work_order(title="Deliver a report")
        await s.commit()
    return {
        "org": org,
        "role_mask": role_mask,
        "public": public,
        "mine": mine,
        "hidden": hidden,
        "readonly": readonly,
        "keys": keys,
        "work_order_id": work_order.id,
    }


_ALLOWED = RateLimitResult(allowed=True, limit=100, remaining=99, retry_after=0)


@pytest.fixture(autouse=True)
def cap(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    """No broker; the daily write cap's Redis calls are recorded, allowed by default."""
    monkeypatch.setattr(doc_tools, "dispatch_ingest", lambda _p: "task-x")
    monkeypatch.setattr(artifact_tools, "dispatch_ingest", lambda _p: "task-x")
    monkeypatch.setattr(doc_tools, "get_redis_client", lambda _settings: MagicMock())
    calls = {"peek": AsyncMock(return_value=_ALLOWED), "count": AsyncMock(return_value=_ALLOWED)}
    monkeypatch.setattr(doc_tools, "peek_rate_limit", calls["peek"])
    monkeypatch.setattr(doc_tools, "check_rate_limit", calls["count"])
    return calls


def _ctx(s: AsyncSession, world: dict[str, Any], key: str | None, grants: dict[str, Any] | None = None) -> ToolContext:
    return ToolContext(
        session=s,
        org_id=world["org"].id,
        settings=SimpleNamespace(api_key_document_writes_per_day=100),
        agent=SimpleNamespace(name="writer", grants=grants or {}),
        actor_user_id=None,  # a key-started run has no actor
        via_api_key=True,
        api_key_id=world["keys"][key] if key else None,
        work_order_id=world["work_order_id"],
    )


async def _call(app_factory: Any, world: dict[str, Any], key: str, args: dict[str, Any]) -> dict[str, Any]:
    async with app_factory() as s:
        await db_scope.enter_tenant(s, world["org"].id)
        out = await doc_tools.CREATE_DOCUMENT.handler(_ctx(s, world, key), args)
        await s.commit()
        return out


async def _docs(seed_factory: Any, org_id: uuid.UUID) -> list[Document]:
    async with seed_factory() as s:
        return list((await s.execute(select(Document).where(Document.org_id == org_id))).scalars())


class TestCreateDocument:
    async def test_writes_into_a_folder_the_key_may_add_to(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any
    ) -> None:
        out = await _call(
            app_factory, world, "writer", {"title": "t", "text": "x", "folder_id": str(world["public"].id)}
        )
        assert "id" in out, out
        docs = await _docs(seed_factory, world["org"].id)
        assert len(docs) == 1
        assert docs[0].uploaded_by_id is None  # a key is not a person

    async def test_refused_without_knowledge_write_on_the_key(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any
    ) -> None:
        out = await _call(
            app_factory, world, "reader", {"title": "t", "text": "x", "folder_id": str(world["public"].id)}
        )
        assert "knowledge:write" in out["error"]
        assert await _docs(seed_factory, world["org"].id) == []

    @pytest.mark.parametrize("folder", ["hidden", "readonly", None])
    async def test_refused_where_the_key_may_not_add(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any, folder: str | None
    ) -> None:
        args: dict[str, Any] = {"title": "t", "text": "x"}
        if folder:
            args["folder_id"] = str(world[folder].id)
        out = await _call(app_factory, world, "writer", args)
        assert "error" in out
        assert await _docs(seed_factory, world["org"].id) == []

    async def test_folder_limited_key_stays_in_its_folders(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any
    ) -> None:
        out = await _call(
            app_factory, world, "mine_only", {"title": "t", "text": "x", "folder_id": str(world["public"].id)}
        )
        assert "outside" in out["error"]
        out = await _call(
            app_factory, world, "mine_only", {"title": "t", "text": "x", "folder_id": str(world["mine"].id)}
        )
        assert "id" in out, out

    async def test_an_org_key_needs_knowledge_write_too(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any
    ) -> None:
        args = {"title": "t", "text": "x", "folder_id": str(world["hidden"].id)}
        out = await _call(app_factory, world, "org", args)
        assert "knowledge:write" in out["error"]
        assert await _docs(seed_factory, world["org"].id) == []
        out = await _call(app_factory, world, "org_writer", args)  # org-wide reach, like the REST write
        assert "id" in out, out

    @pytest.mark.parametrize("key", ["writer", "org_writer"])
    async def test_counts_against_the_keys_daily_cap(
        self, world: dict[str, Any], app_factory: Any, cap: dict[str, AsyncMock], key: str
    ) -> None:
        out = await _call(app_factory, world, key, {"title": "t", "text": "x", "folder_id": str(world["public"].id)})
        assert "id" in out, out
        counted = cap["count"].await_args
        assert counted.args[1] == f"docwrite:{world['keys'][key]}"  # the REST write's counter
        assert counted.kwargs == {"limit": 100, "window_seconds": 86_400}

    async def test_refused_once_the_daily_cap_is_spent(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any, cap: dict[str, AsyncMock]
    ) -> None:
        cap["peek"].return_value = RateLimitResult(allowed=False, limit=100, remaining=0, retry_after=60)
        out = await _call(
            app_factory, world, "writer", {"title": "t", "text": "x", "folder_id": str(world["public"].id)}
        )
        assert "daily" in out["error"]
        assert await _docs(seed_factory, world["org"].id) == []
        cap["count"].assert_not_awaited()

    async def test_refused_once_the_key_is_revoked(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any
    ) -> None:
        async with seed_factory() as s:
            key = await s.get(ApiKey, world["keys"]["writer"])
            key.revoked_at = datetime.now(UTC)
            await s.commit()
        out = await _call(
            app_factory, world, "writer", {"title": "t", "text": "x", "folder_id": str(world["public"].id)}
        )
        assert "error" in out
        assert await _docs(seed_factory, world["org"].id) == []


async def _attach(app_factory: Any, world: dict[str, Any], key: str, folder: str | None) -> dict[str, Any]:
    args: dict[str, Any] = {"title": "Report", "content": "# Findings"}
    if folder:
        args["folder_id"] = str(world[folder].id)
    async with app_factory() as s:
        await db_scope.enter_tenant(s, world["org"].id)
        out = await artifact_tools.ATTACH_DOCUMENT.handler(_ctx(s, world, key), args)
        await s.commit()
        return out


class TestAttachDocument:
    """A work-order deliverable is a knowledge-base write like create_document, so a
    key-started run is held to the same scope, folder and daily-cap rules."""

    async def test_an_org_key_needs_knowledge_write(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any, cap: dict[str, AsyncMock]
    ) -> None:
        out = await _attach(app_factory, world, "org", "public")
        assert "knowledge:write" in out["error"]
        assert await _docs(seed_factory, world["org"].id) == []
        cap["count"].assert_not_awaited()

    @pytest.mark.parametrize("key", ["writer", "org_writer"])
    async def test_counts_against_the_keys_daily_cap(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any, cap: dict[str, AsyncMock], key: str
    ) -> None:
        out = await _attach(app_factory, world, key, "public")
        assert out.get("attached") is True, out
        counted = cap["count"].await_args
        assert counted.args[1] == f"docwrite:{world['keys'][key]}"  # the REST write's counter
        assert counted.kwargs == {"limit": 100, "window_seconds": 86_400}
        docs = await _docs(seed_factory, world["org"].id)
        assert len(docs) == 1
        assert docs[0].uploaded_by_id is None  # a key is not a person

    async def test_refused_once_the_daily_cap_is_spent(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any, cap: dict[str, AsyncMock]
    ) -> None:
        cap["peek"].return_value = RateLimitResult(allowed=False, limit=100, remaining=0, retry_after=60)
        out = await _attach(app_factory, world, "org_writer", "public")
        assert "daily" in out["error"]
        assert await _docs(seed_factory, world["org"].id) == []
        cap["count"].assert_not_awaited()

    @pytest.mark.parametrize("folder", ["hidden", "readonly", None])
    async def test_a_scoped_key_may_not_add_outside_its_reach(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any, folder: str | None
    ) -> None:
        out = await _attach(app_factory, world, "writer", folder)
        assert "error" in out
        assert await _docs(seed_factory, world["org"].id) == []

    async def test_a_folder_limited_key_stays_in_its_folders(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any
    ) -> None:
        out = await _attach(app_factory, world, "mine_only", "public")
        assert "outside" in out["error"]
        out = await _attach(app_factory, world, "mine_only", "mine")
        assert out.get("attached") is True, out

    async def test_refused_once_the_key_is_revoked(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any
    ) -> None:
        async with seed_factory() as s:
            key = await s.get(ApiKey, world["keys"]["org_writer"])
            key.revoked_at = datetime.now(UTC)
            await s.commit()
        out = await _attach(app_factory, world, "org_writer", "public")
        assert "error" in out
        assert await _docs(seed_factory, world["org"].id) == []


class _Brain:
    calls: list[dict[str, Any]] = []

    def __init__(self, _settings: Any) -> None: ...

    async def vector_chat(self, **kwargs: Any) -> dict[str, Any]:
        _Brain.calls.append(kwargs)
        return {"answer": "ok", "sources": []}


@pytest.fixture
def brain(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    _Brain.calls = []

    async def _model(_session: Any, _org: Any) -> None:
        return None

    monkeypatch.setattr("api.services.brain_client.BrainAPIClient", _Brain)
    monkeypatch.setattr("api.services.org_llm.org_default_llm_model", _model)
    return _Brain.calls


class TestSearchKnowledge:
    async def _search(self, app_factory: Any, world: dict[str, Any], key: str | None, grants: Any = None) -> Any:
        async with app_factory() as s:
            await db_scope.enter_tenant(s, world["org"].id)
            return await knowledge_tools.SEARCH_KNOWLEDGE.handler(_ctx(s, world, key, grants), {"query": "q"})

    async def test_org_scope_grant_cannot_make_a_scoped_key_run_unrestricted(
        self, world: dict[str, Any], app_factory: Any, brain: list[dict[str, Any]]
    ) -> None:
        out = await self._search(app_factory, world, "writer", {"knowledge_scope": "org"})
        assert out["answer"] == "ok"
        sent = brain[-1]["access_keys"]
        assert sent is not None and sent
        assert world["role_mask"] in sent
        assert brain[-1]["folder_tags"] is None

    async def test_folder_limited_key_sends_its_folders(
        self, world: dict[str, Any], app_factory: Any, brain: list[dict[str, Any]]
    ) -> None:
        await self._search(app_factory, world, "mine_only", {"knowledge_scope": "org"})
        assert brain[-1]["folder_tags"] == [f"folder:{world['mine'].id}"]

    async def test_org_key_run_keeps_the_unattended_rule(
        self, world: dict[str, Any], app_factory: Any, brain: list[dict[str, Any]]
    ) -> None:
        out = await self._search(app_factory, world, "org")
        assert "error" in out and not brain
        await self._search(app_factory, world, "org", {"knowledge_scope": "org"})
        assert brain[-1]["access_keys"] is None

    @pytest.mark.parametrize("key", ["writer", None])
    async def test_revoked_or_deleted_key_is_refused(
        self, world: dict[str, Any], app_factory: Any, seed_factory: Any, brain: list[dict[str, Any]], key: str | None
    ) -> None:
        async with seed_factory() as s:
            row = await s.get(ApiKey, world["keys"]["writer"])
            row.revoked_at = datetime.now(UTC)
            await s.commit()
        out = await self._search(app_factory, world, key, {"knowledge_scope": "org"})
        assert "error" in out
        assert brain == []
