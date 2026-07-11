"""Unit tests for the entity-records router: cursor codec, validation bounds,
inactive-slug 404, and EntityRecordError → 400 mapping. The repository/session
layer is mocked so no database is required."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.dependencies import OrgContext, require_org_access
from api.config import get_settings
from api.dependencies import get_tenant_db
from api.repositories.dynamic_entity import EntityRecordError, RecordCursor
from api.routers import entity_records
from api.services import entity_records_helpers
from api.services.entity_records_helpers import decode_cursor as _decode_cursor
from api.services.entity_records_helpers import encode_cursor as _encode_cursor
from fastapi import FastAPI, HTTPException


class TestCursorCodec:
    def test_roundtrip(self) -> None:
        from datetime import UTC, datetime

        cur = RecordCursor("created_at", "desc", datetime(2026, 7, 6, 12, 0, tzinfo=UTC), uuid.uuid4())
        back = _decode_cursor(_encode_cursor(cur))
        # order_value serialises to its ISO string form; the rest round-trips exactly.
        assert back.order_slug == cur.order_slug
        assert back.order_dir == cur.order_dir
        assert back.id == cur.id

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
    app.dependency_overrides[get_settings] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestValidationBounds:
    async def test_limit_below_min_422(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/thing/records?limit=0")
        assert resp.status_code == 422

    async def test_limit_above_max_422(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/thing/records?limit=201")
        assert resp.status_code == 422

    async def test_q_too_long_422(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app()) as client:
                resp = await client.get(f"/api/entities/thing/records?q={'x' * 201}")
        assert resp.status_code == 422

    async def test_malformed_cursor_400(self) -> None:
        repo = MagicMock()
        repo.list = AsyncMock(return_value=([], None))
        with patch.object(entity_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/thing/records?cursor=%21%21bad")
        assert resp.status_code == 400


class TestAggregateEndpoint:
    async def test_aggregate_happy_path(self) -> None:
        repo = MagicMock()
        repo.aggregate = AsyncMock(
            return_value={
                "group_by": ["stage"], "metrics": ["count"],
                "rows": [{"stage": "won", "count": 2}], "row_count": 1,
            }
        )
        with patch.object(entity_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app()) as client:
                resp = await client.post(
                    "/api/entities/deal/aggregate",
                    json={"group_by": [{"field": "stage"}], "metrics": [{"op": "count"}]},
                )
        assert resp.status_code == 200
        assert resp.json()["rows"] == [{"stage": "won", "count": 2}]

    async def test_aggregate_entity_error_is_400(self) -> None:
        repo = MagicMock()
        repo.aggregate = AsyncMock(side_effect=EntityRecordError("bad group field"))
        with patch.object(entity_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app()) as client:
                resp = await client.post("/api/entities/deal/aggregate", json={"group_by": [{"field": "ghost"}]})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "bad group field"


class TestErrorMapping:
    async def test_inactive_slug_is_404(self) -> None:
        # build_record_repo (the real _repo_for) raises 404 for an inactive/missing
        # definition — it now lives in services/entity_records_helpers.py.
        defs_repo = MagicMock()
        defs_repo.get_by_slug = AsyncMock(return_value=MagicMock(is_active=False))
        with patch.object(entity_records_helpers, "EntityDefinitionRepository", return_value=defs_repo):
            async with _client(_app()) as client:
                resp = await client.get("/api/entities/inactive/records")
        assert resp.status_code == 404

    async def test_create_record_error_is_400(self) -> None:
        repo = MagicMock()
        repo.create = AsyncMock(side_effect=EntityRecordError("bad payload"))
        with patch.object(entity_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app()) as client:
                resp = await client.post("/api/entities/thing/records", json={"x": 1})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "bad payload"
