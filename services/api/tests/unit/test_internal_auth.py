"""Unit tests for the internal router's shared-secret authentication.

The RLS/org-scoping behaviours (org-scoped key read, 404+rollback on a filtered
row) are DB-backed and live in tests/integration/test_internal_router.py. Here we
only assert the auth gate, which fails before any DB access.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from api.config import get_settings
from api.routers import internal
from fastapi import FastAPI

pytestmark = pytest.mark.unit


def _app(internal_api_key: str) -> FastAPI:
    app = FastAPI()
    app.include_router(internal.router, prefix="/api/internal")
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        internal_api_key=internal_api_key
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_missing_key_is_401() -> None:
    async with _client(_app("configured-secret")) as client:
        resp = await client.get(f"/api/internal/orgs/{uuid.uuid4()}/openai-key")
    assert resp.status_code == 401


async def test_invalid_key_is_401() -> None:
    async with _client(_app("configured-secret")) as client:
        resp = await client.get(
            f"/api/internal/orgs/{uuid.uuid4()}/openai-key",
            headers={"X-Internal-API-Key": "wrong"},
        )
    assert resp.status_code == 401


async def test_unconfigured_key_disables_endpoint_503() -> None:
    async with _client(_app("")) as client:
        resp = await client.get(
            f"/api/internal/orgs/{uuid.uuid4()}/openai-key",
            headers={"X-Internal-API-Key": "anything"},
        )
    assert resp.status_code == 503


async def test_document_status_requires_key() -> None:
    async with _client(_app("configured-secret")) as client:
        resp = await client.post(
            f"/api/internal/documents/{uuid.uuid4()}/status",
            json={"tenant_id": str(uuid.uuid4()), "status": "SUCCESS"},
        )
    assert resp.status_code == 401
