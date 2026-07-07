"""Unit tests for the site-admin Celery status endpoint (/api/admin/celery)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, get_current_user
from api.config import Settings, get_settings
from api.dependencies import get_db, get_redis
from api.routers import admin as admin_module
from api.routers.admin import router as admin_router
from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _stub_inspect(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict]]:
    """Replace the Celery inspect() broadcast + revoke so tests don't hit a broker.

    Returns the mutable ``active`` payload each test can populate; shape mirrors
    ``inspect().active()`` → ``{worker_name: [task_entry, ...]}``.
    """
    payload: dict[str, list[dict]] = {}

    class _Inspector:
        def active(self) -> dict[str, list[dict]]:
            return payload

    monkeypatch.setattr(admin_module.celery_app.control, "inspect", lambda **_k: _Inspector())
    monkeypatch.setattr(admin_module.celery_app.control, "revoke", lambda *_a, **_k: None)
    # Cancel purges partial vectors best-effort — stub the brain client.
    fake_brain = MagicMock()
    fake_brain.remove_document = AsyncMock(return_value={})
    monkeypatch.setattr(admin_module, "BrainAPIClient", lambda _s: fake_brain)
    return payload


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
        self.sets: list[tuple[str, str]] = []

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

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets.append((key, value))


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def first(self):  # noqa: ANN201 - returns a row tuple or None
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Async session stub: SELECTs return the configured rows; writes are no-ops."""

    def __init__(self, rows: list | None = None) -> None:
        self._rows = rows or []
        self.committed = False

    async def execute(self, stmt, params=None):  # noqa: ANN001, ANN201
        if "UPDATE" in str(stmt):
            return _FakeResult([])
        return _FakeResult(self._rows)

    async def commit(self) -> None:
        self.committed = True


def _build_app(
    current: CurrentUser,
    redis: _FakeRedis,
    session: _FakeSession | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/admin")
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[get_db] = lambda: session or _FakeSession()
    app.dependency_overrides[get_settings] = lambda: Settings(secret_key="x")
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


async def test_active_tasks_surfaced_with_document_id(_stub_inspect: dict[str, list[dict]]) -> None:
    _stub_inspect["celery@worker1"] = [
        {
            "id": "task-abc",
            "name": "worker.tasks.extract.task_extract_and_ingest",
            "args": [{"document_id": "doc-42", "tenant_id": "org-1"}],
            "kwargs": {},
        }
    ]
    redis = _FakeRedis(heartbeat=_heartbeat(age_seconds=2))
    async with _client(_build_app(_user(is_site_admin=True), redis)) as client:
        resp = await client.get("/api/admin/celery")
    assert resp.status_code == 200
    active = resp.json()["active"]
    assert len(active) == 1
    assert active[0]["id"] == "task-abc"
    assert active[0]["worker"] == "celery@worker1"
    # document_id is pulled from the ingest payload for the logs deep-link.
    assert active[0]["document_id"] == "doc-42"


async def test_active_document_id_none_for_string_args(_stub_inspect: dict[str, list[dict]]) -> None:
    # Newer Celery reports args as a repr string — not machine-readable → None.
    _stub_inspect["celery@w"] = [{"id": "t", "name": "x", "args": "({'document_id': 'd'},)", "kwargs": "{}"}]
    redis = _FakeRedis(heartbeat=_heartbeat(age_seconds=2))
    async with _client(_build_app(_user(is_site_admin=True), redis)) as client:
        resp = await client.get("/api/admin/celery")
    assert resp.json()["active"][0]["document_id"] is None


class _LogRedis:
    """Redis stand-in whose lrange returns the full list (for the logs endpoint)."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self._lines


async def test_job_logs_requires_site_admin() -> None:
    async with _client(_build_app(_user(is_site_admin=False), _FakeRedis())) as client:
        resp = await client.get("/api/admin/jobs/doc-1/logs")
    assert resp.status_code == 403


async def test_job_logs_parses_lines_and_skips_garbage() -> None:
    lines = [
        json.dumps({"ts": "t1", "level": "info", "stage": "queued", "message": "queued"}),
        "garbage",
        json.dumps({"ts": "t2", "level": "error", "stage": "failed", "message": "boom"}),
    ]
    app = _build_app(_user(is_site_admin=True), _LogRedis(lines))
    async with _client(app) as client:
        resp = await client.get("/api/admin/jobs/doc-9/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["document_id"] == "doc-9"
    assert [e["message"] for e in body["events"]] == ["queued", "boom"]
