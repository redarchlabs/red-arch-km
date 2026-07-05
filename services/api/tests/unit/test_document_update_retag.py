"""Unit tests for PATCH /api/documents/{id} re-tag-on-move behaviour.

When a document's folder changes, the derived vector-store metadata (the
``folder:<id>`` tag + the folder's access_keys) must be re-propagated via a
metadata-update task so folder-scoped retrieval isn't stale. DB + dispatch are
stubbed so this runs without Postgres or a broker.
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
NEW_FOLDER_ID = uuid.uuid4()
DOC_KEY = "doc-key-1"


def _ctx() -> OrgContext:
    user = CurrentUser(
        sub="u", username="u", email="u@x.com", profile_id=uuid.uuid4(), is_site_admin=False
    )
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=True)


class _FakeDoc(SimpleNamespace):
    pass


class _FakeDocRepo:
    def __init__(self, session: Any) -> None: ...
    async def get(self, _id: uuid.UUID) -> _FakeDoc:
        return _FakeDoc(
            id=uuid.uuid4(),
            title="Doc",
            description=None,
            document_key=DOC_KEY,
            processing_status="SUCCESS",
            folder_id=None,
            org_id=ORG_ID,
            created_at=datetime.now(UTC),
            tags=[],
            metadata_={},
            # No per-document permissions of its own → entitlement falls back to
            # the folder's masks on retag (mirrors a freshly-created document).
            viewer_permissions_config=None,
            contributor_permissions_config=None,
        )


class _FakeFolder(SimpleNamespace):
    pass


class _FakeFolderRepo:
    def __init__(self, session: Any) -> None: ...
    async def get(self, folder_id: uuid.UUID) -> _FakeFolder:
        return _FakeFolder(id=folder_id, view_permission_masks=[7, 9])


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"dispatched": None}
    monkeypatch.setattr(documents_module, "DocumentRepository", _FakeDocRepo)
    monkeypatch.setattr(documents_module, "FolderRepository", _FakeFolderRepo)

    def _dispatch(data: dict[str, Any]) -> str:
        state["dispatched"] = data
        return "task-1"

    monkeypatch.setattr(documents_module, "dispatch_metadata_update", _dispatch)
    return state


def _app() -> FastAPI:
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


async def test_folder_move_dispatches_retag_with_new_tag_and_masks(wiring: dict[str, Any]) -> None:
    async with _client(_app()) as client:
        resp = await client.patch(
            f"/api/documents/{uuid.uuid4()}", json={"folder_id": str(NEW_FOLDER_ID)}
        )
    assert resp.status_code == 200

    dispatched = wiring["dispatched"]
    assert dispatched is not None
    assert dispatched["document_key"] == DOC_KEY
    assert dispatched["tenant_id"] == str(ORG_ID)
    assert f"folder:{NEW_FOLDER_ID}" in dispatched["new_tags"]
    assert dispatched["new_access_keys"] == [7, 9]


async def test_description_only_change_does_not_retag(wiring: dict[str, Any]) -> None:
    async with _client(_app()) as client:
        resp = await client.patch(
            f"/api/documents/{uuid.uuid4()}", json={"description": "just a note"}
        )
    assert resp.status_code == 200
    # A pure description edit touches no vector-store metadata → no dispatch.
    assert wiring["dispatched"] is None
