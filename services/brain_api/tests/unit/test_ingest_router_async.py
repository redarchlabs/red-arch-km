"""Tests for the async ingest endpoint: 202 accept + background + status poll.

A minimal app mounts only the ingest router (no lifespan → no real Qdrant/Neo4j),
with the service and the job registry overridden. The document pipeline is a fast
fake, so the background task completes almost immediately and the status endpoint
flips running → done/failed.
"""

from __future__ import annotations

import time
from typing import Any

from brain_api.auth import require_api_key
from brain_api.routers.ingest import _get_service
from brain_api.routers.ingest import router as ingest_router
from brain_api.services.ingest_jobs import IngestJobRegistry, get_ingest_jobs
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeService:
    def __init__(self, *, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        self._result = result if result is not None else {"chunks": 3, "triplets": 2}
        self._error = error

    def ingest_document(self, **_kwargs: Any) -> dict[str, Any]:
        if self._error:
            raise RuntimeError(self._error)
        return self._result


def _app(service: _FakeService, registry: IngestJobRegistry) -> FastAPI:
    app = FastAPI()
    app.include_router(ingest_router, prefix="/api")
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[get_ingest_jobs] = lambda: registry
    app.dependency_overrides[require_api_key] = lambda: "test"
    return app


_PAYLOAD = {"tenant_id": "t1", "document_key": "key-1", "title": "Doc", "text": "hello world"}


def _poll(client: TestClient, tenant: str, key: str, timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = client.get(f"/api/ingest-status/{tenant}/{key}").json()
        if body["state"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("ingest did not leave 'running' in time")


def test_submit_returns_202_and_completes() -> None:
    reg = IngestJobRegistry()
    with TestClient(_app(_FakeService(result={"chunks": 7, "triplets": 1}), reg)) as client:
        resp = client.post("/api/ingest-document", json=_PAYLOAD)
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

        final = _poll(client, "t1", "key-1")
        assert final["state"] == "done"
        assert final["chunks"] == 7
        assert final["triplets"] == 1


def test_background_failure_marks_failed() -> None:
    reg = IngestJobRegistry()
    with TestClient(_app(_FakeService(error="boom"), reg)) as client:
        resp = client.post("/api/ingest-document", json=_PAYLOAD)
        assert resp.status_code == 202

        final = _poll(client, "t1", "key-1")
        assert final["state"] == "failed"
        assert final["error"]


def test_duplicate_submit_while_running_is_ignored() -> None:
    reg = IngestJobRegistry()
    # Pre-mark the job running so the endpoint takes the idempotent branch and
    # does NOT start a second background ingest.
    reg.mark_running("t1", "key-1")
    with TestClient(_app(_FakeService(), reg)) as client:
        resp = client.post("/api/ingest-document", json=_PAYLOAD)
        assert resp.status_code == 202
        assert resp.json()["already_running"] is True


def test_status_unknown_for_untracked_document() -> None:
    reg = IngestJobRegistry()
    with TestClient(_app(_FakeService(), reg)) as client:
        body = client.get("/api/ingest-status/t1/nope").json()
        assert body["state"] == "unknown"
