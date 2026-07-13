"""Unit tests for caller-supplied ``document_key`` on document creation.

Covers the schema-level validation of ``document_key`` and the create-endpoint
behaviour (passthrough to the repository, duplicate → 409). The repository and
Celery dispatch are stubbed, so these run without a DB or broker.

Regression guard for the chat-citation 404 bug: KM2 used to force a random UUID
as ``document_key`` and ignore any caller-supplied value, so documents seeded
into the vector store under a stable slug (e.g. ``privacy-02-pii``) never
resolved back to their Postgres row and every citation 404'd.
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
from api.dependencies import get_tenant_db
from api.routers import documents as documents_module
from api.routers.documents import router as documents_router
from api.schemas.document import DocumentCreate
from fastapi import FastAPI
from pydantic import ValidationError

ORG_ID = uuid.uuid4()
PROFILE_ID = uuid.uuid4()


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #
class TestDocumentKeyValidation:
    @pytest.mark.parametrize(
        "key",
        [
            "privacy-02-pii",
            "security_03_devices",
            "a",
            "MixedCase-123",
            str(uuid.uuid4()),
            "with.dots.and-dashes_ok",
        ],
    )
    def test_accepts_url_and_storage_safe_keys(self, key: str) -> None:
        assert DocumentCreate(title="t", document_key=key).document_key == key

    def test_none_is_allowed_and_defaults_later(self) -> None:
        assert DocumentCreate(title="t").document_key is None
        assert DocumentCreate(title="t", document_key=None).document_key is None

    @pytest.mark.parametrize(
        "key",
        [
            "",  # empty
            "has space",  # whitespace
            "slash/in/key",  # path separator — breaks object-storage key + URL
            "..",  # path traversal
            "a..b",  # embedded traversal
            "unicode-café",  # non-ASCII
            "tab\tchar",
            "x" * 256,  # too long
            "search",  # reserved (collides with /documents/search)
            "upload",  # reserved (collides with /documents/upload)
            "by-key",  # reserved (collides with /documents/by-key)
            "UPLOAD",  # reserved, case-insensitive
        ],
    )
    def test_rejects_unsafe_or_reserved_keys(self, key: str) -> None:
        with pytest.raises(ValidationError):
            DocumentCreate(title="t", document_key=key)


# --------------------------------------------------------------------------- #
# Create endpoint: passthrough + duplicate handling
# --------------------------------------------------------------------------- #
class _FakeDoc(SimpleNamespace):
    pass


class _FakeRepo:
    """Stub repo that echoes the document_key it was created with.

    ``existing_key`` seeds a pre-existing document so the duplicate path can be
    exercised via ``get_by_key``.
    """

    existing_key: str | None = None
    last_create_kwargs: dict[str, Any] | None = None

    def __init__(self, session: Any, org_id: uuid.UUID) -> None:
        self._org_id = org_id

    async def get_by_key(self, document_key: str) -> _FakeDoc | None:
        if _FakeRepo.existing_key is not None and document_key == _FakeRepo.existing_key:
            return _FakeDoc(id=uuid.uuid4(), document_key=document_key)
        return None

    async def create(self, **kwargs: Any) -> _FakeDoc:
        _FakeRepo.last_create_kwargs = kwargs
        return _FakeDoc(
            id=uuid.uuid4(),
            title=kwargs["title"],
            description=kwargs.get("description"),
            document_key=kwargs.get("document_key") or str(uuid.uuid4()),
            processing_status="PENDING",
            folder_id=kwargs.get("folder_id"),
            org_id=self._org_id,
            created_at=datetime.now(UTC),
            document_url=None,
            use_knowledge_graph=None,
            metadata_={},
            text=kwargs.get("text"),
            size_bytes=None,
            celery_task_id=None,
            viewer_permissions_config=None,
            contributor_permissions_config=None,
        )


def _ctx() -> OrgContext:
    user = CurrentUser(
        sub="user_x", username="x", email="x@example.com", profile_id=PROFILE_ID, is_site_admin=False
    )
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=True)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    _FakeRepo.existing_key = None
    _FakeRepo.last_create_kwargs = None
    monkeypatch.setattr(documents_module, "DocumentRepository", _FakeRepo)
    monkeypatch.setattr(documents_module, "dispatch_ingest", lambda payload: "task-1")

    application = FastAPI()
    application.include_router(documents_router, prefix="/api/documents")

    async def _fake_db() -> Any:
        yield AsyncMock()

    application.dependency_overrides[require_org_access] = _ctx
    application.dependency_overrides[get_tenant_db] = _fake_db
    return application


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_create_passes_explicit_key_to_repo(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/documents/", json={"title": "PII", "text": "body", "document_key": "privacy-02-pii"}
        )
    assert resp.status_code == 201
    assert resp.json()["document_key"] == "privacy-02-pii"
    assert _FakeRepo.last_create_kwargs is not None
    assert _FakeRepo.last_create_kwargs["document_key"] == "privacy-02-pii"


async def test_create_without_key_still_gets_a_uuid(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post("/api/documents/", json={"title": "No key", "text": "body"})
    assert resp.status_code == 201
    # A valid UUID was substituted when the caller omitted the key (back-compat).
    uuid.UUID(resp.json()["document_key"])


async def test_create_rejects_duplicate_key_with_409(app: FastAPI) -> None:
    _FakeRepo.existing_key = "privacy-02-pii"
    async with _client(app) as client:
        resp = await client.post(
            "/api/documents/", json={"title": "dup", "text": "body", "document_key": "privacy-02-pii"}
        )
    assert resp.status_code == 409


async def test_create_rejects_invalid_key_with_422(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            "/api/documents/", json={"title": "bad", "text": "body", "document_key": "has/slash"}
        )
    assert resp.status_code == 422
