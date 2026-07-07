"""Unit tests for POST /documents/{id}/cancel and GET /documents/{id}/logs.

The repository, Redis, Celery control, and brain-api purge are monkeypatched, so
these run without a database, broker, or brain-api.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, OrgContext, require_org_access
from api.config import Settings, get_settings
from api.dependencies import get_redis, get_tenant_db
from api.routers import documents as documents_module
from api.routers.documents import router as documents_router
from fastapi import FastAPI

ORG_ID = uuid.uuid4()
OWNER_ID = uuid.uuid4()
OTHER_ID = uuid.uuid4()

# Mutable holder so each test can set the acting user/context.
_CTX: dict[str, OrgContext] = {}


def _ctx(*, profile_id: uuid.UUID, is_admin: bool) -> OrgContext:
    user = CurrentUser(
        sub="u",
        username="u",
        email="u@example.com",
        profile_id=profile_id,
        is_site_admin=False,
    )
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=is_admin)


def _fake_doc(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "title": "doc.pdf",
        "description": None,
        "text": None,
        "document_key": str(uuid.uuid4()),
        "document_url": None,
        "processing_status": "PROCESSING",
        "processing_details": {"stage": "ingesting", "percent": 60},
        "folder_id": None,
        "org_id": ORG_ID,
        "created_at": datetime.now(UTC),
        "size_bytes": 10,
        "uploaded_by_id": OWNER_ID,
        "celery_task_id": "task-123",
        "use_knowledge_graph": None,
        "metadata_": {},
        "viewer_permissions_config": None,
        "contributor_permissions_config": None,
        "view_permission_masks": [],
        "contributor_permission_masks": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeRedis:
    def __init__(self, logs: list[str] | None = None) -> None:
        self._logs = logs or []
        self.sets: list[tuple[str, str, int | None]] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets.append((key, value, ex))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self._logs


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"doc": None, "redis": _FakeRedis(), "revoked": []}

    class _DocRepo:
        def __init__(self, session: Any, org_id: uuid.UUID) -> None: ...

        async def get(self, _doc_id: uuid.UUID) -> Any:
            return state["doc"]

    monkeypatch.setattr(documents_module, "DocumentRepository", _DocRepo)

    fake_celery = MagicMock()
    fake_celery.control.revoke.side_effect = lambda tid, **kw: state["revoked"].append((tid, kw))
    monkeypatch.setattr(documents_module, "celery_app", fake_celery)

    monkeypatch.setattr(documents_module, "_purge_brain_vectors", AsyncMock())
    return state


def _build_app(state: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    app.include_router(documents_router, prefix="/api/documents")

    async def _fake_db() -> Any:
        yield AsyncMock()

    app.dependency_overrides[require_org_access] = lambda: _CTX["ctx"]
    app.dependency_overrides[get_tenant_db] = _fake_db
    app.dependency_overrides[get_redis] = lambda: state["redis"]
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_owner_can_cancel_processing_doc(wiring: dict[str, Any]) -> None:
    doc = _fake_doc()
    wiring["doc"] = doc
    _CTX["ctx"] = _ctx(profile_id=OWNER_ID, is_admin=False)

    async with _client(_build_app(wiring)) as client:
        resp = await client.post(f"/api/documents/{doc.id}/cancel")

    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "CANCELLED"
    # Flag set + task revoked-with-terminate + partial vectors purged.
    assert wiring["redis"].sets and wiring["redis"].sets[0][0] == "ingest:cancel:task-123"
    assert wiring["revoked"] == [("task-123", {"terminate": True, "signal": "SIGTERM"})]
    documents_module._purge_brain_vectors.assert_awaited_once()


async def test_admin_can_cancel_other_users_doc(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(uploaded_by_id=OWNER_ID)
    _CTX["ctx"] = _ctx(profile_id=OTHER_ID, is_admin=True)

    async with _client(_build_app(wiring)) as client:
        resp = await client.post(f"/api/documents/{wiring['doc'].id}/cancel")

    assert resp.status_code == 200


async def test_non_owner_non_admin_forbidden(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(uploaded_by_id=OWNER_ID)
    _CTX["ctx"] = _ctx(profile_id=OTHER_ID, is_admin=False)

    async with _client(_build_app(wiring)) as client:
        resp = await client.post(f"/api/documents/{wiring['doc'].id}/cancel")

    assert resp.status_code == 403
    assert wiring["revoked"] == []  # never attempted


async def test_cancel_conflict_when_already_terminal(wiring: dict[str, Any]) -> None:
    wiring["doc"] = _fake_doc(processing_status="SUCCESS")
    _CTX["ctx"] = _ctx(profile_id=OWNER_ID, is_admin=False)

    async with _client(_build_app(wiring)) as client:
        resp = await client.post(f"/api/documents/{wiring['doc'].id}/cancel")

    assert resp.status_code == 409


async def test_cancel_404_when_missing(wiring: dict[str, Any]) -> None:
    wiring["doc"] = None
    _CTX["ctx"] = _ctx(profile_id=OWNER_ID, is_admin=False)

    async with _client(_build_app(wiring)) as client:
        resp = await client.post(f"/api/documents/{uuid.uuid4()}/cancel")

    assert resp.status_code == 404


async def test_cancel_without_task_id_still_marks_cancelled(wiring: dict[str, Any]) -> None:
    # A PENDING doc whose dispatch failed has no task id — cancel just flips it.
    wiring["doc"] = _fake_doc(processing_status="PENDING", celery_task_id=None)
    _CTX["ctx"] = _ctx(profile_id=OWNER_ID, is_admin=False)

    async with _client(_build_app(wiring)) as client:
        resp = await client.post(f"/api/documents/{wiring['doc'].id}/cancel")

    assert resp.status_code == 200
    assert resp.json()["processing_status"] == "CANCELLED"
    assert wiring["revoked"] == []  # nothing to revoke


async def test_logs_returns_parsed_events(wiring: dict[str, Any]) -> None:
    doc = _fake_doc()
    wiring["doc"] = doc
    wiring["redis"] = _FakeRedis(
        logs=[
            json.dumps({"ts": "2026-07-06T21:00:00+00:00", "level": "info", "stage": "queued", "message": "queued"}),
            "not-json",  # a malformed line is skipped, not fatal
            json.dumps({"ts": "2026-07-06T21:00:05+00:00", "level": "info", "stage": "done", "message": "done"}),
        ]
    )
    _CTX["ctx"] = _ctx(profile_id=OWNER_ID, is_admin=False)

    async with _client(_build_app(wiring)) as client:
        resp = await client.get(f"/api/documents/{doc.id}/logs")

    assert resp.status_code == 200
    body = resp.json()
    assert [e["message"] for e in body["events"]] == ["queued", "done"]


async def test_logs_404_when_missing(wiring: dict[str, Any]) -> None:
    wiring["doc"] = None
    _CTX["ctx"] = _ctx(profile_id=OWNER_ID, is_admin=False)

    async with _client(_build_app(wiring)) as client:
        resp = await client.get(f"/api/documents/{uuid.uuid4()}/logs")

    assert resp.status_code == 404
