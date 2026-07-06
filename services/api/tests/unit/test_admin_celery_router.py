"""Unit tests for the site-admin Celery status endpoint (/api/admin/celery)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from api.auth.dependencies import CurrentUser, get_current_user
from api.dependencies import get_redis
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


class _FakeRedis:
    """Minimal async stand-in exposing only what the endpoint calls."""

    def __init__(
        self,
        *,
        heartbeat: str | None = None,
        schedule: str | None = None,
        items: list[str] | None = None,
    ) -> None:
        self._heartbeat = heartbeat
        self._schedule = schedule
        self._items = items or []

    async def get(self, key: str) -> str | None:
        if key == "celery:beat:heartbeat":
            return self._heartbeat
        if key == "celery:beat:schedule":
            return self._schedule
        return None

    async def llen(self, key: str) -> int:
        return len(self._items)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self._items[start : end + 1]


def _build_app(current: CurrentUser, redis: _FakeRedis) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/admin")
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_user] = lambda: current
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _heartbeat(age_seconds: float, interval: float = 15) -> str:
    ts = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return json.dumps({"ts": ts.isoformat(), "interval": interval})


def _message(task: str, task_id: str = "abc12345def") -> str:
    return json.dumps(
        {"headers": {"task": task, "id": task_id, "argsrepr": "()", "kwargsrepr": "{}"}}
    )


async def test_celery_requires_site_admin() -> None:
    async with _client(_build_app(_user(is_site_admin=False), _FakeRedis())) as client:
        resp = await client.get("/api/admin/celery")
    assert resp.status_code == 403


async def test_beat_down_when_no_heartbeat() -> None:
    redis = _FakeRedis(heartbeat=None)
    async with _client(_build_app(_user(is_site_admin=True), redis)) as client:
        resp = await client.get("/api/admin/celery")
    assert resp.status_code == 200
    body = resp.json()
    assert body["beat"]["status"] == "down"
    assert "beat is not running" in body["beat"]["detail"]


async def test_beat_ok_with_fresh_heartbeat_and_parses_queue() -> None:
    redis = _FakeRedis(
        heartbeat=_heartbeat(age_seconds=3),
        schedule=json.dumps(
            [{"name": "beat-heartbeat", "task": "worker.tasks.monitoring.beat_heartbeat", "schedule_seconds": 15}]
        ),
        items=[_message("worker.tasks.workflow.sweep_outbox"), "not-json-garbage"],
    )
    async with _client(_build_app(_user(is_site_admin=True), redis)) as client:
        resp = await client.get("/api/admin/celery")
    assert resp.status_code == 200
    body = resp.json()
    assert body["beat"]["status"] == "ok"
    assert body["depth"] == 2
    assert body["items"][0]["task"] == "worker.tasks.workflow.sweep_outbox"
    # A malformed message still appears (as an unparseable row) rather than 500.
    assert body["items"][1]["task"] is None
    assert body["schedule"][0]["name"] == "beat-heartbeat"


async def test_beat_stale_when_heartbeat_old() -> None:
    redis = _FakeRedis(heartbeat=_heartbeat(age_seconds=300, interval=15))
    async with _client(_build_app(_user(is_site_admin=True), redis)) as client:
        resp = await client.get("/api/admin/celery")
    assert resp.status_code == 200
    assert resp.json()["beat"]["status"] == "stale"
