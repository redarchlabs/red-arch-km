"""Unit tests for POST /api/documents/upload.

Storage and the Celery dispatch are monkeypatched, so these run without MinIO,
a broker, or a real database (the repository is stubbed too).
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


class _FakeRepo:
    """Stub DocumentRepository whose create returns a fully-attributed doc."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> _FakeDoc:
        return _FakeDoc(
            id=uuid.uuid4(),
            title=kwargs["title"],
            description=kwargs.get("description"),
            document_key=str(uuid.uuid4()),
            processing_status="PENDING",
            folder_id=kwargs.get("folder_id"),
            org_id=kwargs["org_id"],
            created_at=datetime.now(UTC),
            document_url=None,
            use_knowledge_graph=None,
            metadata_={},
        )


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"put": None, "dispatched": None}

    fake_storage = MagicMock()

    def _record_put(key: str, data: bytes, content_type: str) -> None:
        state["put"] = {"key": key, "size": len(data), "content_type": content_type}

    fake_storage.put_object.side_effect = _record_put

    monkeypatch.setattr(documents_module, "StorageClient", lambda settings: fake_storage)
    monkeypatch.setattr(documents_module, "DocumentRepository", _FakeRepo)

    def _dispatch(data: dict[str, Any]) -> str:
        state["dispatched"] = data
        return "task-123"

    monkeypatch.setattr(documents_module, "dispatch_extract_ingest", _dispatch)
    return state


def _build_app(max_mb: int = 50) -> FastAPI:
    app = FastAPI()
    app.include_router(documents_router, prefix="/api/documents")

    async def _fake_db() -> Any:
        yield AsyncMock()

    app.dependency_overrides[require_org_access] = _ctx
    app.dependency_overrides[get_tenant_db] = _fake_db
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x", max_file_size_mb=max_mb)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_upload_rejects_unsupported_extension(wiring: dict[str, Any]) -> None:
    async with _client(_build_app()) as client:
        resp = await client.post(
            "/api/documents/upload",
            files={"file": ("malware.exe", b"MZ...", "application/octet-stream")},
            data={"title": "bad", "translation_method": "ocr"},
        )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]
    assert wiring["put"] is None  # never stored


async def test_upload_rejects_oversize(wiring: dict[str, Any]) -> None:
    big = b"x" * (2 * 1024 * 1024)  # 2 MB, over the 1 MB cap
    async with _client(_build_app(max_mb=1)) as client:
        resp = await client.post(
            "/api/documents/upload",
            files={"file": ("scan.pdf", big, "application/pdf")},
            data={"title": "big", "translation_method": "ocr"},
        )
    assert resp.status_code == 413
    assert wiring["put"] is None


async def test_upload_rejects_bad_translation_method(wiring: dict[str, Any]) -> None:
    async with _client(_build_app()) as client:
        resp = await client.post(
            "/api/documents/upload",
            files={"file": ("scan.pdf", b"%PDF", "application/pdf")},
            data={"title": "x", "translation_method": "magic"},
        )
    assert resp.status_code == 400
    assert "translation_method" in resp.json()["detail"]


async def test_upload_happy_path_stores_and_dispatches(wiring: dict[str, Any]) -> None:
    async with _client(_build_app()) as client:
        resp = await client.post(
            "/api/documents/upload",
            files={"file": ("scan.pdf", b"%PDF-1.4 body", "application/pdf")},
            data={"title": "My Scan", "translation_method": "ai"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "My Scan"
    assert body["processing_status"] == "PENDING"

    # Stored at {org_id}/{document_key}/{filename}
    put = wiring["put"]
    assert put is not None
    parts = put["key"].split("/")
    assert parts[0] == str(ORG_ID)
    assert parts[-1] == "scan.pdf"
    assert len(parts) == 3
    assert put["content_type"] == "application/pdf"

    dispatched = wiring["dispatched"]
    assert dispatched is not None
    assert dispatched["translation_method"] == "ai"
    assert dispatched["filename"] == "scan.pdf"
    assert dispatched["document_url"] == put["key"]
    assert dispatched["tenant_id"] == str(ORG_ID)
    # Secrets must never ride the payload.
    assert "openai_api_key" not in dispatched


async def test_upload_filename_path_traversal_is_stripped(wiring: dict[str, Any]) -> None:
    async with _client(_build_app()) as client:
        resp = await client.post(
            "/api/documents/upload",
            files={"file": ("../../etc/passwd.txt", b"root:x:0:0", "text/plain")},
            data={"title": "x", "translation_method": "ocr"},
        )
    assert resp.status_code == 201
    assert wiring["put"]["key"].split("/")[-1] == "passwd.txt"
