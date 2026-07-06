"""Unit tests for PUT /api/documents/{id}/content (the Markdown editor save).

Storage, the repositories, and the Celery dispatches are monkeypatched, so these
run without MinIO, a broker, or a real database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.routers import documents as documents_module
from api.routers.documents import router as documents_router
from fastapi import FastAPI

ORG_ID = uuid.uuid4()
PROFILE_ID = uuid.uuid4()
FOLDER_ID = uuid.uuid4()


def _ctx() -> OrgContext:
    user = CurrentUser(
        sub="user_x",
        username="x",
        email="x@example.com",
        profile_id=PROFILE_ID,
        is_site_admin=False,
    )
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=True)


class _FakeDoc(SimpleNamespace):
    pass


def _fake_doc(**overrides: Any) -> _FakeDoc:
    """A document row stub carrying every attribute the endpoint touches."""
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "title": "notes.md",
        "description": None,
        "text": None,
        "document_key": str(uuid.uuid4()),
        "document_url": None,
        "processing_status": "SUCCESS",
        "folder_id": None,
        "org_id": ORG_ID,
        "created_at": datetime.now(UTC),
        "size_bytes": 10,
        "use_knowledge_graph": None,
        "metadata_": {},
        "viewer_permissions_config": None,
        "contributor_permissions_config": None,
        "view_permission_masks": [],
        "contributor_permission_masks": [],
    }
    base.update(overrides)
    return _FakeDoc(**base)


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"doc": None, "folder": None, "put": None, "ingest": None, "extract": None}

    fake_storage = MagicMock()

    def _record_put(key: str, data: bytes, content_type: str) -> None:
        state["put"] = {"key": key, "data": data, "content_type": content_type}

    fake_storage.put_object.side_effect = _record_put
    monkeypatch.setattr(documents_module, "StorageClient", lambda settings: fake_storage)

    # Re-ingest purges existing vectors first (brain-api ingest appends, so a
    # missing purge would duplicate chunks). Mock the client and record calls.
    fake_brain = MagicMock()
    fake_brain.remove_document = AsyncMock(return_value={})
    state["brain"] = fake_brain
    monkeypatch.setattr(documents_module, "BrainAPIClient", lambda settings: fake_brain)

    class _DocRepo:
        def __init__(self, session: Any, org_id: uuid.UUID) -> None: ...

        async def get(self, _doc_id: uuid.UUID) -> Any:
            return state["doc"]

    class _FolderRepo:
        def __init__(self, session: Any, org_id: uuid.UUID) -> None: ...

        async def get(self, _folder_id: uuid.UUID) -> Any:
            return state["folder"]

        async def effective_view_masks(self, folder: Any) -> list[int]:
            return list(folder.view_permission_masks or []) if folder else []

    monkeypatch.setattr(documents_module, "DocumentRepository", _DocRepo)
    monkeypatch.setattr(documents_module, "FolderRepository", _FolderRepo)

    def _ingest(data: dict[str, Any]) -> str:
        state["ingest"] = data
        return "task-ingest"

    def _extract(data: dict[str, Any]) -> str:
        state["extract"] = data
        return "task-extract"

    monkeypatch.setattr(documents_module, "dispatch_ingest", _ingest)
    monkeypatch.setattr(documents_module, "dispatch_extract_ingest", _extract)
    return state


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(documents_router, prefix="/api/documents")

    async def _fake_db() -> Any:
        yield AsyncMock()

    app.dependency_overrides[require_org_access] = _ctx
    app.dependency_overrides[get_tenant_db] = _fake_db
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_edit_inline_doc_updates_text_and_reingests(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(document_url=None, folder_id=FOLDER_ID)
    wiring["folder"] = SimpleNamespace(id=FOLDER_ID, view_permission_masks=[7])

    async with _client(_build_app()) as client:
        resp = await client.put(f"/api/documents/{uuid.uuid4()}/content", json={"text": "# Hello\n"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["processing_status"] == "PENDING"
    assert body["size_bytes"] == len(b"# Hello\n")

    # Inline path re-ingests text directly; the object store is never touched.
    assert wiring["put"] is None
    assert wiring["extract"] is None
    ingest = wiring["ingest"]
    assert ingest is not None
    assert ingest["text"] == "# Hello\n"
    assert f"folder:{FOLDER_ID}" in ingest["tags"]
    # No per-doc viewer config → entitlement falls back to the folder's masks.
    assert ingest["access_keys"] == [7]
    # Existing vectors are purged before re-ingest so edits replace, not append.
    wiring["brain"].remove_document.assert_awaited_once()


async def test_edit_doc_with_own_perms_uses_doc_masks_not_folder(wiring: dict[str, Any]) -> None:
    # A document that carries its own viewer permissions must re-ingest with ITS
    # masks, not the folder's — per-document permissions take precedence.
    wiring["doc"] = _fake_doc(
        document_url=None,
        folder_id=FOLDER_ID,
        viewer_permissions_config=[{"role": "manager"}],
        view_permission_masks=[42],
    )
    wiring["folder"] = SimpleNamespace(id=FOLDER_ID, view_permission_masks=[7])

    async with _client(_build_app()) as client:
        resp = await client.put(f"/api/documents/{uuid.uuid4()}/content", json={"text": "# Hi\n"})

    assert resp.status_code == 200
    ingest = wiring["ingest"]
    assert ingest is not None
    assert ingest["access_keys"] == [42]
    assert f"folder:{FOLDER_ID}" in ingest["tags"]


async def test_edit_stored_markdown_rewrites_object_and_reextracts(wiring: dict[str, Any]) -> None:
    key = f"{ORG_ID}/deadbeef/notes.md"
    wiring["doc"] = _fake_doc(document_url=key, title="notes.md")

    async with _client(_build_app()) as client:
        resp = await client.put(f"/api/documents/{uuid.uuid4()}/content", json={"text": "new body"})

    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "PENDING"

    put = wiring["put"]
    assert put is not None
    assert put["key"] == key  # rewritten in place, same object key
    assert put["data"] == b"new body"
    assert put["content_type"] == "text/markdown"

    # Storage-backed path re-extracts; the text-only ingest is not used.
    assert wiring["ingest"] is None
    extract = wiring["extract"]
    assert extract is not None
    assert extract["filename"] == "notes.md"
    assert extract["document_url"] == key
    wiring["brain"].remove_document.assert_awaited_once()


async def test_edit_non_text_original_is_rejected(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(document_url=f"{ORG_ID}/deadbeef/scan.pdf", title="scan.pdf")

    async with _client(_build_app()) as client:
        resp = await client.put(f"/api/documents/{uuid.uuid4()}/content", json={"text": "x"})

    assert resp.status_code == 415
    assert wiring["put"] is None
    assert wiring["extract"] is None
    assert wiring["ingest"] is None


async def test_edit_missing_document_returns_404(wiring: dict[str, Any]) -> None:
    wiring["doc"] = None
    async with _client(_build_app()) as client:
        resp = await client.put(f"/api/documents/{uuid.uuid4()}/content", json={"text": "x"})
    assert resp.status_code == 404


async def test_clearing_inline_text_persists_without_reingest(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(document_url=None, text="old content", size_bytes=11)

    async with _client(_build_app()) as client:
        resp = await client.put(f"/api/documents/{uuid.uuid4()}/content", json={"text": ""})

    assert resp.status_code == 200
    assert resp.json()["size_bytes"] is None
    assert wiring["ingest"] is None
    assert wiring["extract"] is None
