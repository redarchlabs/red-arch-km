"""The first-party document routes reject reserved metadata keys up front.

brain-api drops index-owned fields (``access_keys``, ``tenant_id``, ``tags``,
``document_key``, ``type`` …) from caller metadata at ingest
(``RESERVED_INGEST_METADATA_KEYS``), and the public API already rejects them.
``POST /api/documents`` and ``PATCH /api/documents/{id}`` accepted and stored them
silently; they now return 422 naming the offending keys — through the same
validator as the public write and the agent tool.

A PATCH is checked against the STORED metadata, not in the schema: a document
stored before the rule (holding, say, its own ``document_key`` in metadata) stays
editable, including by a client that re-sends the metadata it loaded. Only a
reserved key that the PATCH adds or changes is refused.
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
from api.schemas.document import DocumentCreate, DocumentUpdate
from api.schemas.reserved_metadata import (
    RESERVED_METADATA_KEYS,
    changed_reserved_metadata_keys,
    reject_reserved_metadata_keys,
    strip_reserved_metadata_keys,
)
from fastapi import FastAPI
from pydantic import ValidationError

ORG_ID = uuid.uuid4()
_RESERVED = sorted(RESERVED_METADATA_KEYS)


class TestRejectReservedMetadataKeys:
    def test_allows_ordinary_metadata(self) -> None:
        meta = {"author": "Ada", "folder_path": "Ops/Manuals", "attributes": {"k": "v"}}
        assert reject_reserved_metadata_keys(meta) == meta

    def test_error_lists_every_offending_key(self) -> None:
        with pytest.raises(ValueError, match="access_keys, tenant_id"):
            reject_reserved_metadata_keys({"tenant_id": "x", "access_keys": [0], "author": "Ada"})


class TestStripReservedMetadataKeys:
    def test_returns_a_new_dict_and_the_dropped_keys(self) -> None:
        meta = {"access_keys": [0], "tenant_id": "other", "author": "Ada"}
        kept, dropped = strip_reserved_metadata_keys(meta)
        assert kept == {"author": "Ada"}
        assert dropped == ["access_keys", "tenant_id"]
        assert meta == {"access_keys": [0], "tenant_id": "other", "author": "Ada"}  # not mutated

    def test_none_is_empty(self) -> None:
        assert strip_reserved_metadata_keys(None) == ({}, [])


class TestChangedReservedMetadataKeys:
    def test_unchanged_legacy_key_is_not_a_change(self) -> None:
        stored = {"document_key": "k", "author": "Ada"}
        assert changed_reserved_metadata_keys({"document_key": "k", "author": "Bob"}, stored) == []

    def test_added_and_changed_keys_are_listed_sorted(self) -> None:
        stored = {"document_key": "k"}
        sent = {"document_key": "other", "access_keys": [0], "author": "Ada"}
        assert changed_reserved_metadata_keys(sent, stored) == ["access_keys", "document_key"]

    def test_no_stored_metadata(self) -> None:
        assert changed_reserved_metadata_keys({"tags": ["x"]}, None) == ["tags"]

    def test_dropping_a_legacy_key_is_not_a_change(self) -> None:
        assert changed_reserved_metadata_keys({"author": "Ada"}, {"document_key": "k"}) == []


class TestDocumentSchemas:
    @pytest.mark.parametrize("key", _RESERVED)
    def test_create_rejects(self, key: str) -> None:
        with pytest.raises(ValidationError, match=key):
            DocumentCreate(title="t", metadata={key: "x"})

    @pytest.mark.parametrize("key", _RESERVED)
    def test_update_schema_defers_to_the_route(self, key: str) -> None:
        # Whether a reserved key is new or an unchanged legacy one depends on the
        # stored document, so PATCH checks it in the route, not the schema.
        assert DocumentUpdate(metadata={key: "x"}).metadata == {key: "x"}

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
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=True)


def _make_app() -> FastAPI:
    application = FastAPI()
    application.include_router(documents_router, prefix="/api/documents")

    async def _fake_db() -> Any:
        yield AsyncMock()

    application.dependency_overrides[require_org_access] = _ctx
    application.dependency_overrides[get_tenant_db] = _fake_db
    application.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    return application


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(documents_module, "DocumentRepository", _ExplodingRepo)

    def _no_dispatch(_payload: dict[str, Any]) -> str:
        msg = "ingest must not be dispatched when metadata is rejected"
        raise AssertionError(msg)

    monkeypatch.setattr(documents_module, "dispatch_ingest", _no_dispatch)
    monkeypatch.setattr(documents_module, "dispatch_metadata_update", _no_dispatch)
    return _make_app()


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("key", ["access_keys", "tenant_id", "document_key", "tags", "type"])
async def test_create_rejects_reserved_metadata_with_422(app: FastAPI, key: str) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/documents/", json={"title": "t", "text": "body", "metadata": {key: [0]}})
    assert resp.status_code == 422
    assert key in resp.text


# --------------------------------------------------------------------------- #
# A document already holding a reserved key stays editable
# --------------------------------------------------------------------------- #
_LEGACY_KEY = "legacy-doc"


class _LegacyDocRepo:
    """A document stored before the rule, with its own key copied into metadata."""

    doc: SimpleNamespace | None = None
    stored_metadata: dict[str, Any] = {"document_key": _LEGACY_KEY, "author": "Ada"}

    def __init__(self, session: Any, org_id: uuid.UUID) -> None: ...

    async def get(self, _id: uuid.UUID) -> SimpleNamespace:
        _LegacyDocRepo.doc = SimpleNamespace(
            id=uuid.uuid4(),
            title="Old title",
            description=None,
            document_key=_LEGACY_KEY,
            processing_status="SUCCESS",
            folder_id=None,
            org_id=ORG_ID,
            created_at=datetime.now(UTC),
            tags=[],
            metadata_=dict(_LegacyDocRepo.stored_metadata),
            viewer_permissions_config=None,
            contributor_permissions_config=None,
        )
        return _LegacyDocRepo.doc


class _NoFolderRepo:
    def __init__(self, session: Any, org_id: uuid.UUID) -> None: ...

    async def get(self, _id: uuid.UUID) -> None:
        return None

    async def effective_view_masks(self, _folder: Any) -> list[int]:
        return []


async def test_patch_without_metadata_keeps_a_legacy_documents_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(documents_module, "DocumentRepository", _LegacyDocRepo)
    monkeypatch.setattr(documents_module, "FolderRepository", _NoFolderRepo)
    monkeypatch.setattr(documents_module, "dispatch_metadata_update", lambda _p: "task-1")

    async with _client(_make_app()) as client:
        resp = await client.patch(f"/api/documents/{uuid.uuid4()}", json={"title": "New title"})

    assert resp.status_code == 200, resp.text
    assert _LegacyDocRepo.doc is not None
    assert _LegacyDocRepo.doc.title == "New title"
    assert _LegacyDocRepo.doc.metadata_ == {"document_key": _LEGACY_KEY, "author": "Ada"}  # untouched


@pytest.fixture
def legacy_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(documents_module, "DocumentRepository", _LegacyDocRepo)
    monkeypatch.setattr(documents_module, "FolderRepository", _NoFolderRepo)
    monkeypatch.setattr(documents_module, "dispatch_metadata_update", lambda _p: "task-1")
    monkeypatch.setattr(_LegacyDocRepo, "doc", None)
    return _make_app()


async def test_patch_resending_unchanged_legacy_metadata_succeeds(legacy_app: FastAPI) -> None:
    """A client that PATCHes back the metadata it loaded (legacy key and all) is
    not refused for a key it did not change."""
    sent = {"document_key": _LEGACY_KEY, "author": "Grace"}
    async with _client(legacy_app) as client:
        resp = await client.patch(f"/api/documents/{uuid.uuid4()}", json={"title": "New", "metadata": sent})

    assert resp.status_code == 200, resp.text
    assert _LegacyDocRepo.doc is not None
    assert _LegacyDocRepo.doc.metadata_ == sent
    assert _LegacyDocRepo.doc.title == "New"


@pytest.mark.parametrize(
    ("sent", "named"),
    [
        ({"document_key": "someone-else", "author": "Ada"}, "document_key"),  # changed
        ({"document_key": _LEGACY_KEY, "access_keys": [0]}, "access_keys"),  # added
        ({"tenant_id": "other"}, "tenant_id"),  # added
    ],
)
async def test_patch_adding_or_changing_a_reserved_key_is_422(
    legacy_app: FastAPI, sent: dict[str, Any], named: str
) -> None:
    async with _client(legacy_app) as client:
        resp = await client.patch(f"/api/documents/{uuid.uuid4()}", json={"title": "New", "metadata": sent})

    assert resp.status_code == 422, resp.text
    assert named in resp.text
    assert _LegacyDocRepo.doc is not None
    # Refused before anything was applied.
    assert _LegacyDocRepo.doc.metadata_ == {"document_key": _LEGACY_KEY, "author": "Ada"}
    assert _LegacyDocRepo.doc.title == "Old title"


@pytest.mark.parametrize("key", ["access_keys", "tenant_id", "document_key", "tags", "type"])
async def test_patch_rejects_reserved_metadata_on_a_clean_document(
    legacy_app: FastAPI, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setattr(_LegacyDocRepo, "stored_metadata", {"author": "Ada"})
    async with _client(legacy_app) as client:
        resp = await client.patch(f"/api/documents/{uuid.uuid4()}", json={"metadata": {key: [0]}})
    assert resp.status_code == 422
    assert key in resp.text
