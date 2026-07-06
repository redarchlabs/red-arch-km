"""Unit tests for the entity-records router: cursor codec, validation bounds,
inactive-slug 404, and EntityRecordError → 400 mapping. The repository/session
layer is mocked so no database is required."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.dependencies import OrgContext, require_org_access
from api.dependencies import get_tenant_db
from api.repositories.dynamic_entity import EntityRecordError
from api.routers import entity_records
from api.routers.entity_records import _decode_cursor, _encode_cursor
from fastapi import FastAPI, HTTPException


class TestCursorCodec:
    def test_roundtrip(self) -> None:
        from datetime import UTC, datetime

        cur = (datetime(2026, 7, 6, 12, 0, tzinfo=UTC), uuid.uuid4())
        assert _decode_cursor(_encode_cursor(cur)) == cur

    def test_malformed_cursor_is_400(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _decode_cursor("!!!not-base64-or-valid!!!")
        assert exc.value.status_code == 400


def _ctx() -> OrgContext:
    return OrgContext(
        user=MagicMock(), org_id=uuid.uuid4(), membership=MagicMock(), is_org_admin=True
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(entity_records.router, prefix="/api/entities")
    app.dependency_overrides[require_org_access] = _ctx
    app.dependency_overrides[get_tenant_db] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestValidationBounds:
    async def test_limit_below_min_422(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "_repo_for", AsyncMock(return_value=repo)):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/thing/records?limit=0")
        assert resp.status_code == 422

    async def test_limit_above_max_422(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "_repo_for", AsyncMock(return_value=repo)):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/thing/records?limit=201")
        assert resp.status_code == 422

    async def test_q_too_long_422(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "_repo_for", AsyncMock(return_value=repo)):
            async with _client(_app()) as client:
                resp = await client.get(f"/api/entities/thing/records?q={'x' * 201}")
        assert resp.status_code == 422

    async def test_malformed_cursor_400(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "_repo_for", AsyncMock(return_value=repo)):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/thing/records?cursor=%21%21bad")
        assert resp.status_code == 400


class TestErrorMapping:
    async def test_inactive_slug_is_404(self) -> None:
        # _repo_for (real) raises 404 for an inactive/missing definition.
        defs_repo = MagicMock()
        defs_repo.get_by_slug = AsyncMock(return_value=MagicMock(is_active=False))
        with patch.object(entity_records, "EntityDefinitionRepository", return_value=defs_repo):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/inactive/records")
        assert resp.status_code == 404

    async def test_create_record_error_is_400(self) -> None:
        repo = MagicMock()
        repo.create = AsyncMock(side_effect=EntityRecordError("bad payload"))
        with patch.object(entity_records, "_repo_for", AsyncMock(return_value=repo)):
            async with _client(_app()) as client:
                resp = await client.post("/api/entities/thing/records", json={"x": 1})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "bad payload"
