"""Unit tests for the entity-definitions router: EntityError → HTTP status
mapping via ``_raise_http`` / ``_ERROR_STATUS``. Service + session are mocked."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_db
from api.routers import entity_definitions
from api.routers.entity_definitions import _ERROR_STATUS, _raise_http
from api.services.entity_service import (
    EntityConflictError,
    EntityLimitError,
    EntityNotFoundError,
    EntityValidationError,
)
from fastapi import FastAPI, HTTPException


class TestRaiseHttpMapping:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (EntityConflictError("dup"), 409),
            (EntityLimitError("too many"), 409),
            (EntityNotFoundError("missing"), 404),
            (EntityValidationError("bad"), 400),
            (RuntimeError("unknown"), 400),  # default fallback
        ],
    )
    def test_each_error_maps_to_status(self, exc: Exception, expected: int) -> None:
        with pytest.raises(HTTPException) as raised:
            _raise_http(exc)
        assert raised.value.status_code == expected

    def test_error_status_table_covers_all_entity_errors(self) -> None:
        assert _ERROR_STATUS[EntityConflictError] == 409
        assert _ERROR_STATUS[EntityLimitError] == 409
        assert _ERROR_STATUS[EntityNotFoundError] == 404
        assert _ERROR_STATUS[EntityValidationError] == 400


def _ctx() -> OrgContext:
    return OrgContext(
        user=MagicMock(), org_id=uuid.uuid4(), membership=MagicMock(), is_org_admin=True
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(entity_definitions.router, prefix="/api/entity-definitions")
    app.dependency_overrides[require_org_admin] = _ctx
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestCreateDefinitionErrorMapping:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (EntityConflictError("slug exists"), 409),
            (EntityLimitError("limit"), 409),
            (EntityValidationError("bad"), 400),
        ],
    )
    async def test_service_error_maps_to_status(self, exc: Exception, expected: int) -> None:
        service = MagicMock()
        service.create_definition = AsyncMock(side_effect=exc)
        with patch.object(entity_definitions, "EntityService", return_value=service):
            async with _client(_app()) as client:
                resp = await client.post(
                    "/api/entity-definitions/", json={"name": "Thing", "slug": "thing", "fields": []}
                )
        assert resp.status_code == expected
