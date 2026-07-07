"""Unit tests for POST /api/documents/{id}/reprocess.

Storage, repositories, brain-api, and the Celery dispatches are monkeypatched,
so these run without MinIO, a broker, or a real database. The key behaviours:
reprocess works for ANY document type (unlike PUT /content), purges the existing
index before re-dispatching, and refuses while an ingest is in flight.
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


def _ctx(*, is_org_admin: bool = True, profile_id: uuid.UUID = PROFILE_ID) -> OrgContext:
    user = CurrentUser(
        sub="user_x",
        username="x",
        email="x@example.com",
        profile_id=profile_id,
        is_site_admin=False,
    )
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=is_org_admin)


class _FakeDoc(SimpleNamespace):
    pass


def _fake_doc(**overrides: Any) -> _FakeDoc:
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
        "uploaded_by_id": PROFILE_ID,
        "celery_task_id": None,
    }
    base.update(overrides)
    return _FakeDoc(**base)


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"doc": None, "folder": None, "ingest": None, "extract": None}

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


def _build_app(ctx: OrgContext | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(documents_router, prefix="/api/documents")

    async def _fake_db() -> Any:
        yield AsyncMock()

    app.dependency_overrides[require_org_access] = lambda: ctx or _ctx()
    app.dependency_overrides[get_tenant_db] = _fake_db
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_reprocess_file_doc_purges_then_reextracts(wiring: dict[str, Any]) -> None:
    # A PDF (rejected by PUT /content) MUST be reprocessable — that's the point.
    key = f"{ORG_ID}/deadbeef/scan.pdf"
    wiring["doc"] = _fake_doc(document_url=key, title="scan.pdf", folder_id=FOLDER_ID)
    wiring["folder"] = SimpleNamespace(id=FOLDER_ID, view_permission_masks=[7])

    async with _client(_build_app()) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")

    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "PENDING"
    # Purge happens before re-dispatch (ingest is not idempotent).
    wiring["brain"].remove_document.assert_awaited_once()
    extract = wiring["extract"]
    assert extract is not None
    assert extract["document_url"] == key
    assert extract["filename"] == "scan.pdf"
    assert extract["translation_method"] == "ocr"
    assert f"folder:{FOLDER_ID}" in extract["tags"]
    assert extract["access_keys"] == [7]
    assert wiring["ingest"] is None


async def test_reprocess_inline_text_doc_reingests_text(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(document_url=None, text="# Body\n")

    async with _client(_build_app()) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")

    assert resp.status_code == 200
    wiring["brain"].remove_document.assert_awaited_once()
    ingest = wiring["ingest"]
    assert ingest is not None
    assert ingest["text"] == "# Body\n"
    assert wiring["extract"] is None


async def test_reprocess_uses_persisted_translation_method(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(
        document_url=f"{ORG_ID}/deadbeef/scan.pdf",
        metadata_={"translation_method": "ai"},
    )
    async with _client(_build_app()) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")
    assert resp.status_code == 200
    assert wiring["extract"]["translation_method"] == "ai"


async def test_reprocess_in_flight_conflicts(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(document_url="k", processing_status="PROCESSING")

    async with _client(_build_app()) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")

    assert resp.status_code == 409
    # Nothing purged or dispatched while an ingest is still running.
    wiring["brain"].remove_document.assert_not_awaited()
    assert wiring["extract"] is None
    assert wiring["ingest"] is None


async def test_reprocess_requires_owner_or_admin(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(document_url="k", uploaded_by_id=uuid.uuid4())
    ctx = _ctx(is_org_admin=False, profile_id=uuid.uuid4())  # neither owner nor admin

    async with _client(_build_app(ctx)) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")

    assert resp.status_code == 403
    wiring["brain"].remove_document.assert_not_awaited()


async def test_reprocess_owner_non_admin_allowed(wiring: dict[str, Any]) -> None:
    owner = uuid.uuid4()
    wiring["doc"] = _fake_doc(document_url="k", uploaded_by_id=owner)
    ctx = _ctx(is_org_admin=False, profile_id=owner)

    async with _client(_build_app(ctx)) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")

    assert resp.status_code == 200


async def test_reprocess_no_source_returns_422(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(document_url=None, text=None)

    async with _client(_build_app()) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")

    assert resp.status_code == 422
    wiring["brain"].remove_document.assert_not_awaited()


async def test_reprocess_missing_document_returns_404(wiring: dict[str, Any]) -> None:
    wiring["doc"] = None
    async with _client(_build_app()) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/reprocess")
    assert resp.status_code == 404
