"""The first-party document routes reject reserved metadata keys up front.

brain-api drops index-owned fields (``access_keys``, ``tenant_id``, ``tags``,
``document_key``, ``type`` …) from caller metadata at ingest
(``RESERVED_INGEST_METADATA_KEYS``), and the public API already rejects them.
``POST /api/documents`` and ``PATCH /api/documents/{id}`` accepted and stored them
silently; they now return 422 naming the offending keys.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_tenant_db
from api.routers import documents as documents_module
from api.routers.documents import router as documents_router
from api.schemas.document import DocumentCreate, DocumentUpdate
from api.schemas.knowledge_write import RESERVED_METADATA_KEYS, reject_reserved_metadata_keys
from fastapi import FastAPI
from pydantic import ValidationError

ORG_ID = uuid.uuid4()
_RESERVED = sorted(RESERVED_METADATA_KEYS)


class TestReservedKeySet:
    def test_matches_brain_api_reserved_set(self) -> None:
        """The API's copy must not drift from the set brain-api strips at ingest."""
        ingest = pytest.importorskip("brain_api.services.ingest_service")
        assert ingest.RESERVED_INGEST_METADATA_KEYS == RESERVED_METADATA_KEYS


class TestRejectReservedMetadataKeys:
    def test_allows_ordinary_metadata(self) -> None:
        meta = {"author": "Ada", "folder_path": "Ops/Manuals", "attributes": {"k": "v"}}
        assert reject_reserved_metadata_keys(meta) == meta

    def test_error_lists_every_offending_key(self) -> None:
        with pytest.raises(ValueError, match="access_keys, tenant_id"):
            reject_reserved_metadata_keys({"tenant_id": "x", "access_keys": [0], "author": "Ada"})


class TestDocumentSchemas:
    @pytest.mark.parametrize("key", _RESERVED)
    def test_create_rejects(self, key: str) -> None:
        with pytest.raises(ValidationError, match=key):
            DocumentCreate(title="t", metadata={key: "x"})

    @pytest.mark.parametrize("key", _RESERVED)
    def test_update_rejects(self, key: str) -> None:
        with pytest.raises(ValidationError, match=key):
            DocumentUpdate(metadata={key: "x"})

    def test_create_keeps_nested_first_party_metadata(self) -> None:
        # The internal routes keep their looser shape (the upload form nests
        # ``attributes``); only the reserved-key rule is shared with the public API.
        meta = {"attributes": {"region": "EU"}}
        assert DocumentCreate(title="t", metadata=meta).metadata == meta

    def test_update_allows_null_and_omitted(self) -> None:
        assert DocumentUpdate(metadata=None).metadata is None
        assert DocumentUpdate().metadata is None


# --------------------------------------------------------------------------- #
# Routes return 422 and never reach the repository or the ingest queue
# --------------------------------------------------------------------------- #
class _ExplodingRepo:
    def __init__(self, session: Any, org_id: uuid.UUID) -> None:
        msg = "repository must not be reached when metadata is rejected"
        raise AssertionError(msg)


def _ctx() -> OrgContext:
    user = CurrentUser(sub="u", username="u", email="u@x.com", profile_id=uuid.uuid4(), is_site_admin=False)
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=False)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(documents_module, "DocumentRepository", _ExplodingRepo)

    def _no_dispatch(_payload: dict[str, Any]) -> str:
        msg = "ingest must not be dispatched when metadata is rejected"
        raise AssertionError(msg)

    monkeypatch.setattr(documents_module, "dispatch_ingest", _no_dispatch)
    monkeypatch.setattr(documents_module, "dispatch_metadata_update", _no_dispatch)

    application = FastAPI()
    application.include_router(documents_router, prefix="/api/documents")

    async def _fake_db() -> Any:
        yield AsyncMock()

    application.dependency_overrides[require_org_access] = _ctx
    application.dependency_overrides[get_tenant_db] = _fake_db
    application.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    return application


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("key", ["access_keys", "tenant_id", "document_key", "tags", "type"])
async def test_create_rejects_reserved_metadata_with_422(app: FastAPI, key: str) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/documents/", json={"title": "t", "text": "body", "metadata": {key: [0]}})
    assert resp.status_code == 422
    assert key in resp.text


@pytest.mark.parametrize("key", ["access_keys", "tenant_id", "document_key", "tags", "type"])
async def test_patch_rejects_reserved_metadata_with_422(app: FastAPI, key: str) -> None:
    async with _client(app) as client:
        resp = await client.patch(f"/api/documents/{uuid.uuid4()}", json={"metadata": {key: [0]}})
    assert resp.status_code == 422
    assert key in resp.text
