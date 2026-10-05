"""POST /api/v1/knowledge/documents on real PostgreSQL.

The write reuses the internal ingest pipeline: the row is committed, then the
Celery dispatch runs (patched here to a recorder), so what is asserted is the
document row and the exact ingest payload — including the ``access_keys`` the
document's chunks and facts will carry in brain-api.

Covers create, ``external_ref`` versioning (same ref + new content = new version
of the same document; same content = no-op), the permission rules for
scoped keys (dimension assignments, optionally narrowed to folders) and org keys.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from access_mask import MAX_GROUP, MAX_REGION, MAX_ROLE, encode
from api.auth import api_key as ak
from api.dependencies import get_db, get_redis
from api.models.api_key import ApiKey, api_key_folders, api_key_roles
from api.models.document import Document, Folder
from api.models.org import Org, Role
from api.routers import v1 as v1_router
from api.routers.v1 import knowledge as v1_knowledge
from api.services import knowledge_write
from api.services.api_key_assignments import KeyAssignments
from api.services.api_key_service import ApiKeyService, ApiKeyValidationError, generate_key
from api.services.api_rate_limit import RateLimitResult
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_ALLOWED = RateLimitResult(allowed=True, limit=600, remaining=599, retry_after=0)


@dataclass
class World:
    org: Org
    role: Role
    own_mask: int
    hr_mask: int
    public_folder: Folder
    hr_folder: Folder
    readonly_folder: Folder
    contrib_folder: Folder
    scoped_key: str
    folder_key: str
    org_key: str


@pytest_asyncio.fixture
async def seed_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """Superuser sessions for seeding and inspecting rows (bypass RLS)."""
    seed_engine = create_async_engine(database_url)
    yield async_sessionmaker(seed_engine, expire_on_commit=False)
    await seed_engine.dispose()


@pytest_asyncio.fixture
async def app_factory(database_url: str, engine: AsyncEngine) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """The app's own sessions, logged in as the non-superuser ``app_user``.

    Production connects as ``km_app``, which RLS binds; the testcontainers login is
    a superuser, which RLS ignores. Starting every connection as ``app_user`` keeps
    the test honest about what a commit does: it ends the transaction, drops the
    ``SET LOCAL`` tenant scope, and any later read sees nothing until the scope is
    re-entered. Depends on ``engine`` so the schema exists first.
    """
    app_engine = create_async_engine(database_url, connect_args={"server_settings": {"role": "app_user"}})
    yield async_sessionmaker(app_engine, expire_on_commit=False)
    await app_engine.dispose()


async def _folder(
    s: AsyncSession, org: Org, name: str, view: list[int] | None, contrib: list[int] | None = None
) -> Folder:
    folder = Folder(
        name=name,
        org_id=org.id,
        dot_path=name,
        viewer_permissions_config=[{"note": "v"}] if view is not None else None,
        view_permission_masks=view or [],
        contributor_permissions_config=[{"note": "c"}] if contrib is not None else None,
        contributor_permission_masks=contrib or [],
    )
    s.add(folder)
    await s.flush()
    return folder


async def _key(s: AsyncSession, org: Org, *, role: Role | None = None, folders: tuple[Folder, ...] = ()) -> str:
    """An org key, or a scoped key holding ``role`` (and narrowed to ``folders``)."""
    generated = generate_key()
    key = ApiKey(
        name=f"k-{uuid.uuid4().hex[:6]}",
        key_prefix=generated.prefix,
        key_hash=generated.key_hash,
        scopes=["knowledge:write", "knowledge:read"],
        org_id=org.id,
        access_mode="scoped" if role or folders else "org",
    )
    s.add(key)
    await s.flush()
    if role is not None:
        await s.execute(api_key_roles.insert().values(api_key_id=key.id, role_id=role.id))
    for folder in folders:
        await s.execute(api_key_folders.insert().values(api_key_id=key.id, folder_id=folder.id))
    return generated.plaintext


@pytest_asyncio.fixture
async def world(seed_factory: async_sessionmaker[AsyncSession]) -> World:
    org_number = 1000 + (uuid.uuid4().int % 500)
    async with seed_factory() as s:
        org = Org(name=f"KW-{uuid.uuid4().hex[:8]}", permission_number=org_number)
        s.add(org)
        await s.flush()
        role = Role(name="Writer", org_id=org.id, permission_number=3)
        s.add(role)
        await s.flush()
        # One of the masks a key holding only the Writer role reads with.
        own_mask = encode(org=org_number, region=0, dept=0, role=3, group=0)
        hr_mask = encode(org=org_number, region=MAX_REGION, dept=5, role=MAX_ROLE, group=MAX_GROUP)
        public_folder = await _folder(s, org, "Reports", None)
        hr_folder = await _folder(s, org, "HR", [hr_mask])
        # Visible to the scoped key, but only HR may add to it.
        readonly_folder = await _folder(s, org, "Archive", None, contrib=[hr_mask])
        # Visible AND the scoped key is a listed contributor.
        contrib_folder = await _folder(s, org, "Projects", [own_mask], contrib=[own_mask])
        scoped_key = await _key(s, org, role=role)
        folder_key = await _key(s, org, role=role, folders=(contrib_folder,))
        org_key = await _key(s, org)
        await s.commit()
    return World(
        org=org,
        role=role,
        own_mask=own_mask,
        hr_mask=hr_mask,
        public_folder=public_folder,
        hr_folder=hr_folder,
        readonly_folder=readonly_folder,
        contrib_folder=contrib_folder,
        scoped_key=scoped_key,
        folder_key=folder_key,
        org_key=org_key,
    )


@dataclass
class Pipeline:
    ingest: MagicMock
    extract: MagicMock
    purge: AsyncMock
    storage: MagicMock
    metadata_update: MagicMock


@pytest_asyncio.fixture
async def client(
    app_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[tuple[httpx.AsyncClient, Pipeline]]:
    app = FastAPI()
    app.include_router(v1_router.router, prefix="/api/v1")
    app.dependency_overrides[get_redis] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    pipeline = Pipeline(
        ingest=MagicMock(side_effect=lambda _p: f"task-{uuid.uuid4().hex[:6]}"),
        extract=MagicMock(side_effect=lambda _p: f"task-{uuid.uuid4().hex[:6]}"),
        purge=AsyncMock(return_value={}),
        storage=MagicMock(),
        metadata_update=MagicMock(return_value="task-meta"),
    )
    brain = MagicMock()
    brain.remove_document = pipeline.purge
    with (
        patch.object(ak, "get_session_factory", lambda _settings: app_factory),
        patch.object(ak, "check_rate_limit", AsyncMock(return_value=_ALLOWED)),
        patch.object(knowledge_write, "dispatch_ingest", pipeline.ingest),
        patch.object(knowledge_write, "dispatch_extract_ingest", pipeline.extract),
        patch.object(knowledge_write, "BrainAPIClient", lambda _settings: brain),
        patch.object(knowledge_write, "StorageClient", lambda _settings: pipeline.storage),
        patch.object(knowledge_write, "dispatch_metadata_update", pipeline.metadata_update),
        patch.object(v1_knowledge, "check_rate_limit", AsyncMock(return_value=_ALLOWED)),
        patch.object(v1_knowledge, "peek_rate_limit", AsyncMock(return_value=_ALLOWED)),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield http, pipeline


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _row(seed_factory: async_sessionmaker[AsyncSession], doc_id: str) -> Document:
    async with seed_factory() as s:
        return (await s.execute(select(Document).where(Document.id == uuid.UUID(doc_id)))).scalar_one()


async def _set_status(seed_factory: async_sessionmaker[AsyncSession], doc_id: str, status: str) -> None:
    async with seed_factory() as s:
        await s.execute(
            text("UPDATE documents SET processing_status = :s WHERE id = :id"), {"s": status, "id": uuid.UUID(doc_id)}
        )
        await s.commit()


def _body(folder: Folder, content: str = "v1 content", **over: Any) -> dict[str, Any]:
    return {"folder_id": str(folder.id), "title": "Weekly report", "content": content, **over}


class TestCreate:
    async def test_json_create_reuses_the_ingest_pipeline(self, world: World, client: Any, seed_factory: Any) -> None:
        http, pipe = client
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.contrib_folder, external_ref="proj-weekly-W41", metadata={"source": "external-agent"}),
            headers=_auth(world.scoped_key),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["outcome"] == "created"
        assert body["processing_status"] == "PENDING"
        assert body["external_ref"] == "proj-weekly-W41"

        payload = pipe.ingest.call_args.args[0]
        # The chunks + facts inherit the folder's masks: this is what brain-api filters on.
        assert payload["access_keys"] == [world.own_mask]
        assert payload["tags"] == [f"folder:{world.contrib_folder.id}"]
        assert payload["text"] == "v1 content"
        assert payload["metadata"] == {"source": "external-agent"}

        row = await _row(seed_factory, body["id"])
        assert row.external_ref == "proj-weekly-W41"
        assert row.content_hash == hashlib.sha256(b"v1 content").hexdigest()
        assert row.uploaded_by_id is None  # a key is not a person
        assert row.celery_task_id is not None

    async def test_multipart_file_create(self, world: World, client: Any, seed_factory: Any) -> None:
        http, pipe = client
        resp = await http.post(
            "/api/v1/knowledge/documents",
            data={"folder_id": str(world.public_folder.id), "external_ref": "proj-charter"},
            files={"file": ("charter.md", b"# Charter\nScope.", "text/markdown")},
            headers=_auth(world.scoped_key),
        )
        assert resp.status_code == 201, resp.text
        row = await _row(seed_factory, resp.json()["id"])
        assert row.title == "charter"
        assert row.document_url == f"{world.org.id}/{row.document_key}/charter.md"
        assert row.size_bytes == len(b"# Charter\nScope.")
        pipe.storage.put_object.assert_called_once()
        payload = pipe.extract.call_args.args[0]
        assert payload["document_url"] == row.document_url
        assert payload["access_keys"] == []  # public folder → public within the org

    async def test_subfolder_inherits_its_parents_restriction(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        """A folder with no viewer config of its own inherits the nearest configured
        ancestor's masks. Resolving that needs the ancestor rows, which RLS only
        shows inside the tenant scope — so it must happen before the commit that
        ends the scope, or the document would be ingested as public."""
        http, pipe = client
        async with seed_factory() as s:
            payroll = Folder(name="Payroll", org_id=world.org.id, parent_id=world.hr_folder.id, dot_path="HR.Payroll")
            s.add(payroll)
            await s.commit()
        resp = await http.post("/api/v1/knowledge/documents", json=_body(payroll), headers=_auth(world.org_key))
        assert resp.status_code == 201, resp.text
        assert pipe.ingest.call_args.args[0]["access_keys"] == [world.hr_mask]

    async def test_scoped_key_cannot_write_into_a_subfolder_of_a_hidden_folder(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, pipe = client
        async with seed_factory() as s:
            sub = Folder(name="Reviews", org_id=world.org.id, parent_id=world.hr_folder.id, dot_path="HR.Reviews")
            s.add(sub)
            await s.commit()
        resp = await http.post("/api/v1/knowledge/documents", json=_body(sub), headers=_auth(world.scoped_key))
        assert resp.status_code == 404
        pipe.ingest.assert_not_called()

    async def test_unknown_folder_is_404(self, world: World, client: Any) -> None:
        http, _ = client
        body = {"folder_id": str(uuid.uuid4()), "title": "t", "content": "x"}
        resp = await http.post("/api/v1/knowledge/documents", json=body, headers=_auth(world.org_key))
        assert resp.status_code == 404


class TestVersioning:
    async def test_same_external_ref_new_content_is_a_new_version(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "week 1", external_ref="weekly"),
            headers=_auth(world.org_key),
        )
        assert first.status_code == 201
        await _set_status(seed_factory, first.json()["id"], "SUCCESS")

        second = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "week 2", external_ref="weekly", title="Weekly report v2"),
            headers=_auth(world.org_key),
        )
        assert second.status_code == 200, second.text
        assert second.json()["outcome"] == "updated"
        assert second.json()["id"] == first.json()["id"]  # same document, not a duplicate

        row = await _row(seed_factory, first.json()["id"])
        assert row.text == "week 2"
        assert row.title == "Weekly report v2"
        assert row.processing_status == "PENDING"
        assert row.content_hash == hashlib.sha256(b"week 2").hexdigest()
        # The old index is purged before re-ingest (ingest is not idempotent).
        pipe.purge.assert_awaited_once_with(str(world.org.id), row.document_key)
        assert pipe.ingest.call_count == 2
        assert pipe.ingest.call_args.args[0]["text"] == "week 2"

        async with seed_factory() as s:
            await set_tenant(s, str(world.org.id))
            count = (
                await s.execute(
                    text("SELECT count(*) FROM documents WHERE org_id = :o AND external_ref = 'weekly'"),
                    {"o": world.org.id},
                )
            ).scalar_one()
        assert count == 1

    async def test_identical_content_is_a_noop(self, world: World, client: Any, seed_factory: Any) -> None:
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "same", external_ref="charter"),
            headers=_auth(world.org_key),
        )
        await _set_status(seed_factory, first.json()["id"], "SUCCESS")
        again = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "same", external_ref="charter"),
            headers=_auth(world.org_key),
        )
        assert again.status_code == 200
        assert again.json()["outcome"] == "unchanged"
        assert again.json()["id"] == first.json()["id"]
        assert pipe.ingest.call_count == 1
        pipe.purge.assert_not_awaited()

    async def test_identical_content_after_a_failed_ingest_is_retried(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "same", external_ref="retry-me"),
            headers=_auth(world.org_key),
        )
        await _set_status(seed_factory, first.json()["id"], "FAILED")
        again = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "same", external_ref="retry-me"),
            headers=_auth(world.org_key),
        )
        assert again.status_code == 200
        assert again.json()["outcome"] == "reprocessed"
        assert pipe.ingest.call_count == 2

    async def test_new_content_while_previous_version_is_processing_is_409(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, pipe = client
        await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "a", external_ref="busy"),
            headers=_auth(world.org_key),
        )  # left PENDING
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "b", external_ref="busy"),
            headers=_auth(world.org_key),
        )
        assert resp.status_code == 409
        assert pipe.ingest.call_count == 1

    async def test_same_ref_in_another_folder_is_a_separate_document(self, world: World, client: Any) -> None:
        """Refs are unique per folder: the same ref elsewhere is a new document, so
        a write never reveals what a ref names in a folder the caller may not see."""
        http, _ = client
        a = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "a", external_ref="weekly"),
            headers=_auth(world.org_key),
        )
        b = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.contrib_folder, "b", external_ref="weekly"),
            headers=_auth(world.org_key),
        )
        assert a.status_code == 201 and b.status_code == 201, b.text
        assert a.json()["id"] != b.json()["id"]

    async def test_identical_content_with_a_new_title_updates_metadata_only(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "same", external_ref="titled", metadata={"v": 1}),
            headers=_auth(world.org_key),
        )
        await _set_status(seed_factory, first.json()["id"], "SUCCESS")
        again = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "same", external_ref="titled", title="Renamed", metadata={"v": 2}),
            headers=_auth(world.org_key),
        )
        assert again.status_code == 200, again.text
        assert again.json()["outcome"] == "metadata_updated"
        row = await _row(seed_factory, first.json()["id"])
        assert row.title == "Renamed"
        assert row.metadata_ == {"v": 2}
        assert row.processing_status == "SUCCESS"  # no re-ingest
        assert pipe.ingest.call_count == 1
        pipe.purge.assert_not_awaited()
        sent = pipe.metadata_update.call_args.args[0]
        assert sent["title"] == "Renamed"
        assert sent["new_access_keys"] == []
        assert sent["new_tags"] == [f"folder:{world.public_folder.id}"]

    async def test_stale_pending_without_a_task_is_retryable(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        """A broker outage can leave a version PENDING with no task. It must not
        block every later version with 409 forever."""
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "a", external_ref="stuck"),
            headers=_auth(world.org_key),
        )
        async with seed_factory() as s:
            await s.execute(
                text(
                    "UPDATE documents SET processing_status = 'PENDING', celery_task_id = NULL, "
                    "updated_at = now() - interval '5 minutes' WHERE id = :id"
                ),
                {"id": uuid.UUID(first.json()["id"])},
            )
            await s.commit()
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "b", external_ref="stuck"),
            headers=_auth(world.org_key),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["outcome"] == "updated"
        assert pipe.ingest.call_count == 2

    async def test_fresh_pending_without_a_task_is_still_in_flight(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        """Just committed, task id not recorded yet: that is a dispatch in progress."""
        http, _ = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "a", external_ref="fresh"),
            headers=_auth(world.org_key),
        )
        async with seed_factory() as s:
            await s.execute(
                text("UPDATE documents SET celery_task_id = NULL WHERE id = :id"), {"id": uuid.UUID(first.json()["id"])}
            )
            await s.commit()
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "b", external_ref="fresh"),
            headers=_auth(world.org_key),
        )
        assert resp.status_code == 409

    async def test_concurrent_new_versions_ingest_once(self, world: World, client: Any, seed_factory: Any) -> None:
        """The row is locked for the replace: two simultaneous new versions of one
        ref cannot both purge and re-ingest."""
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "v1", external_ref="race"),
            headers=_auth(world.org_key),
        )
        await _set_status(seed_factory, first.json()["id"], "SUCCESS")
        import asyncio

        results = await asyncio.gather(
            http.post(
                "/api/v1/knowledge/documents",
                json=_body(world.public_folder, "v2", external_ref="race"),
                headers=_auth(world.org_key),
            ),
            http.post(
                "/api/v1/knowledge/documents",
                json=_body(world.public_folder, "v3", external_ref="race"),
                headers=_auth(world.org_key),
            ),
        )
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 409], [r.text for r in results]
        assert pipe.ingest.call_count == 2  # the create + exactly one new version
        assert pipe.purge.await_count == 1

    async def test_failed_enqueue_marks_the_document_failed_and_is_503(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, pipe = client
        pipe.ingest.side_effect = RuntimeError("broker down")
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "a", external_ref="no-broker"),
            headers=_auth(world.org_key),
        )
        assert resp.status_code == 503
        async with seed_factory() as s:
            row = (
                await s.execute(
                    select(Document).where(Document.org_id == world.org.id, Document.external_ref == "no-broker")
                )
            ).scalar_one()
        assert row.processing_status == "FAILED"
        # Retrying the same content re-runs it.
        pipe.ingest.side_effect = lambda _p: "task-ok"
        retry = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "a", external_ref="no-broker"),
            headers=_auth(world.org_key),
        )
        assert retry.status_code == 200
        assert retry.json()["outcome"] == "reprocessed"

    async def test_failed_purge_is_503_and_leaves_the_old_version(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "old", external_ref="purge-fails"),
            headers=_auth(world.org_key),
        )
        await _set_status(seed_factory, first.json()["id"], "SUCCESS")
        pipe.purge.side_effect = RuntimeError("brain down")
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "new", external_ref="purge-fails"),
            headers=_auth(world.org_key),
        )
        assert resp.status_code == 503
        row = await _row(seed_factory, first.json()["id"])
        assert row.text == "old"
        assert row.processing_status == "SUCCESS"
        assert pipe.ingest.call_count == 1

    async def test_file_versions_use_distinct_object_keys(self, world: World, client: Any, seed_factory: Any) -> None:
        """A replacement never overwrites the original before the commit, so a
        failed replace cannot destroy the version still being served."""
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            data={"folder_id": str(world.public_folder.id), "external_ref": "spec"},
            files={"file": ("spec.md", b"v1", "text/markdown")},
            headers=_auth(world.org_key),
        )
        await _set_status(seed_factory, first.json()["id"], "SUCCESS")
        old = (await _row(seed_factory, first.json()["id"])).document_url
        second = await http.post(
            "/api/v1/knowledge/documents",
            data={"folder_id": str(world.public_folder.id), "external_ref": "spec"},
            files={"file": ("spec.md", b"v2", "text/markdown")},
            headers=_auth(world.org_key),
        )
        assert second.status_code == 200, second.text
        new = (await _row(seed_factory, first.json()["id"])).document_url
        assert new != old
        assert new.endswith("/spec.md")
        pipe.storage.delete_object.assert_called_once_with(old)

    async def test_external_refs_are_per_org(self, world: World, client: Any, seed_factory: Any) -> None:
        """Same ref in two orgs is two documents (RLS + the per-org unique index)."""
        http, _ = client
        async with seed_factory() as s:
            other = Org(name=f"KW2-{uuid.uuid4().hex[:8]}", permission_number=world.org.permission_number)
            s.add(other)
            await s.flush()
            folder = await _folder(s, other, "Reports", None)
            other_key = await _key(s, other)
            await s.commit()
        a = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "a", external_ref="shared-ref"),
            headers=_auth(world.org_key),
        )
        b = await http.post(
            "/api/v1/knowledge/documents",
            json={"folder_id": str(folder.id), "title": "t", "content": "a", "external_ref": "shared-ref"},
            headers=_auth(other_key),
        )
        assert a.status_code == 201 and b.status_code == 201
        assert a.json()["id"] != b.json()["id"]


class TestPermissions:
    async def test_scoped_key_cannot_write_to_a_folder_it_cannot_see(self, world: World, client: Any) -> None:
        http, pipe = client
        resp = await http.post(
            "/api/v1/knowledge/documents", json=_body(world.hr_folder), headers=_auth(world.scoped_key)
        )
        assert resp.status_code == 404  # indistinguishable from a folder that does not exist
        pipe.ingest.assert_not_called()

    async def test_scoped_key_needs_contributor_rights_when_the_folder_sets_them(
        self, world: World, client: Any
    ) -> None:
        http, pipe = client
        resp = await http.post(
            "/api/v1/knowledge/documents", json=_body(world.readonly_folder), headers=_auth(world.scoped_key)
        )
        assert resp.status_code == 403
        pipe.ingest.assert_not_called()

    async def test_scoped_key_cannot_overwrite_a_document_it_cannot_see(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        """An org key files a doc into a public folder with its own HR-only viewer
        override; the scoped key must not be able to replace it by ref."""
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "secret", external_ref="hr-note"),
            headers=_auth(world.org_key),
        )
        async with seed_factory() as s:
            await s.execute(
                text(
                    "UPDATE documents SET viewer_permissions_config = '[{}]'::jsonb, "
                    "view_permission_masks = ARRAY[:m]::bigint[], processing_status = 'SUCCESS' WHERE id = :id"
                ),
                {"m": world.hr_mask, "id": uuid.UUID(first.json()["id"])},
            )
            await s.commit()
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "overwrite", external_ref="hr-note"),
            headers=_auth(world.scoped_key),
        )
        assert resp.status_code == 409
        # The same text as any other ref conflict: nothing says the document exists
        # but is hidden from this key.
        assert resp.json()["detail"] == knowledge_write.REF_CONFLICT_DETAIL
        assert "access" not in resp.json()["detail"]
        assert pipe.ingest.call_count == 1

    async def test_org_key_may_write_anywhere_in_the_org(self, world: World, client: Any) -> None:
        """Org keys are org-admin-equivalent for writes (documented)."""
        http, _ = client
        for folder in (world.hr_folder, world.readonly_folder):
            resp = await http.post("/api/v1/knowledge/documents", json=_body(folder), headers=_auth(world.org_key))
            assert resp.status_code == 201, resp.text


class TestFolderLimitedKeys:
    async def test_writes_inside_its_folder_and_subfolders(self, world: World, client: Any, seed_factory: Any) -> None:
        http, _ = client
        async with seed_factory() as s:
            sub = Folder(
                name="Weekly", org_id=world.org.id, parent_id=world.contrib_folder.id, dot_path="Projects.Weekly"
            )
            s.add(sub)
            await s.commit()
        for folder in (world.contrib_folder, sub):
            resp = await http.post("/api/v1/knowledge/documents", json=_body(folder), headers=_auth(world.folder_key))
            assert resp.status_code == 201, resp.text

    async def test_a_folder_outside_its_list_is_404(self, world: World, client: Any) -> None:
        """The public folder is visible to the key's role and open to contributors,
        but the key lists only Projects: folders only narrow."""
        http, pipe = client
        resp = await http.post(
            "/api/v1/knowledge/documents", json=_body(world.public_folder), headers=_auth(world.folder_key)
        )
        assert resp.status_code == 404
        pipe.ingest.assert_not_called()


class TestMintingLimitsScopedKeyScopes:
    """A scoped key may hold only scope-aware scopes, and never a wildcard."""

    @pytest.mark.parametrize(
        "scopes", [["*"], ["knowledge:*"], ["records:read"], ["search:read", "workflows:run"], ["config:read"]]
    )
    async def test_unaware_or_wildcard_scopes_refused(self, world: World, seed_factory: Any, scopes: list[str]) -> None:
        async with seed_factory() as s:
            with pytest.raises(ApiKeyValidationError, match="dimension or folder"):
                await ApiKeyService(s, world.org.id).create_key(
                    name="k",
                    scopes=scopes,
                    expires_at=None,
                    created_by_profile_id=None,
                    assignments=KeyAssignments.of(roles=[world.role.id]),
                )
            await s.rollback()

    async def test_org_keys_keep_every_scope(self, world: World, seed_factory: Any) -> None:
        async with seed_factory() as s:
            key, _ = await ApiKeyService(s, world.org.id).create_key(
                name="k", scopes=["*", "records:write"], expires_at=None, created_by_profile_id=None
            )
            assert key.access_mode == "org"
            await s.rollback()


class TestReplaceHonoursDocumentContributorMasks:
    async def test_document_contributor_override_blocks_a_scoped_key(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        """The folder lets the key's role add, but this document's own contributor
        config names only HR: the key may not replace it."""
        http, pipe = client
        first = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "v1", external_ref="locked"),
            headers=_auth(world.scoped_key),
        )
        assert first.status_code == 201
        async with seed_factory() as s:
            await s.execute(
                text(
                    "UPDATE documents SET contributor_permissions_config = '[{}]'::jsonb, "
                    "contributor_permission_masks = ARRAY[:m]::bigint[], processing_status = 'SUCCESS' WHERE id = :id"
                ),
                {"m": world.hr_mask, "id": uuid.UUID(first.json()["id"])},
            )
            await s.commit()
        resp = await http.post(
            "/api/v1/knowledge/documents",
            json=_body(world.public_folder, "v2", external_ref="locked"),
            headers=_auth(world.scoped_key),
        )
        assert resp.status_code == 403
        assert pipe.ingest.call_count == 1


class TestDailyCapCountsOnlyRealWrites:
    async def test_unauthorised_and_unchanged_writes_are_not_counted(
        self, world: World, client: Any, seed_factory: Any
    ) -> None:
        http, _ = client
        counted: list[str] = []

        async def _count(_redis: object, key: str, **_kw: object) -> RateLimitResult:
            counted.append(key)
            return _ALLOWED

        with patch.object(v1_knowledge, "check_rate_limit", AsyncMock(side_effect=_count)):
            # Refused by folder permissions: not counted.
            await http.post("/api/v1/knowledge/documents", json=_body(world.hr_folder), headers=_auth(world.scoped_key))
            assert counted == []
            # A real write: counted once.
            first = await http.post(
                "/api/v1/knowledge/documents",
                json=_body(world.public_folder, "same", external_ref="cap"),
                headers=_auth(world.org_key),
            )
            assert first.status_code == 201
            assert len(counted) == 1
            await _set_status(seed_factory, first.json()["id"], "SUCCESS")
            # Identical content: a no-op, not counted.
            again = await http.post(
                "/api/v1/knowledge/documents",
                json=_body(world.public_folder, "same", external_ref="cap"),
                headers=_auth(world.org_key),
            )
            assert again.json()["outcome"] == "unchanged"
            assert len(counted) == 1

    async def test_exhausted_cap_is_429_before_the_body_is_read(self, world: World, client: Any) -> None:
        http, pipe = client
        blocked = RateLimitResult(allowed=False, limit=500, remaining=0, retry_after=60)
        with patch.object(v1_knowledge, "peek_rate_limit", AsyncMock(return_value=blocked)):
            resp = await http.post(
                "/api/v1/knowledge/documents",
                json=_body(world.public_folder, "x", external_ref="over"),
                headers=_auth(world.org_key),
            )
        assert resp.status_code == 429
        pipe.ingest.assert_not_called()
