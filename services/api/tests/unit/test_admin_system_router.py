"""Unit tests for the site-admin system status endpoint (/api/admin/system)."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, get_current_user
from api.config import Settings, get_settings
from api.dependencies import get_db, get_redis
from api.routers import admin as admin_module
from api.routers.admin import router as admin_router
from fastapi import FastAPI


def _user(*, is_site_admin: bool) -> CurrentUser:
    return CurrentUser(
        sub="user_x",
        username="x",
        email="x@example.com",
        profile_id=uuid.uuid4(),
        is_site_admin=is_site_admin,
    )


def _build_app(current: CurrentUser) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/admin")
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_redis] = lambda: AsyncMock()
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
    app.dependency_overrides[get_current_user] = lambda: current
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def patch_probes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"redis_fails": False}

    async def _ok_db(session: Any) -> admin_module.ComponentStatus:
        return admin_module.ComponentStatus(status="ok", latency_ms=1.0)

    async def _redis(redis: Any) -> admin_module.ComponentStatus:
        if state["redis_fails"]:
            return admin_module.ComponentStatus(status="error", detail="connection refused")
        return admin_module.ComponentStatus(status="ok", latency_ms=1.0)

    async def _ok_brain(settings: Any) -> admin_module.ComponentStatus:
        return admin_module.ComponentStatus(status="ok", latency_ms=2.0)

    async def _queue(redis: Any) -> admin_module.ComponentStatus:
        return admin_module.ComponentStatus(status="ok", detail="depth=3")

    monkeypatch.setattr(admin_module, "_probe_db", _ok_db)
    monkeypatch.setattr(admin_module, "_probe_redis", _redis)
    monkeypatch.setattr(admin_module, "_probe_brain_api", _ok_brain)
    monkeypatch.setattr(admin_module, "_probe_celery_queue", _queue)
    return state


async def test_system_requires_site_admin(patch_probes: dict[str, Any]) -> None:
    async with _client(_build_app(_user(is_site_admin=False))) as client:
        resp = await client.get("/api/admin/system")
    assert resp.status_code == 403


async def test_system_reports_all_components(patch_probes: dict[str, Any]) -> None:
    async with _client(_build_app(_user(is_site_admin=True))) as client:
        resp = await client.get("/api/admin/system")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["components"]) == {"database", "redis", "brain_api", "worker_queue"}
    assert body["components"]["database"]["status"] == "ok"
    assert body["version"]


async def test_system_degraded_component_does_not_fail_endpoint(patch_probes: dict[str, Any]) -> None:
    patch_probes["redis_fails"] = True
    async with _client(_build_app(_user(is_site_admin=True))) as client:
        resp = await client.get("/api/admin/system")
    assert resp.status_code == 200
    assert resp.json()["components"]["redis"]["status"] == "error"
