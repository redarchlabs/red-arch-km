"""HTTP-level tests for the assembled ``/api/v1`` enterprise surface.

These exercise the real ``api.routers.v1.router`` (not synthetic endpoints), so
they lock in the wiring the isolated auth/scope/rate-limit tests can't: that each
route declares the CORRECT scope, that the router-level rate limiter engages on a
real route, and that each router maps its service errors to the right HTTP status.

The API-key principal + rate-limit backend are overridden/patched; the per-domain
services the routers delegate to are mocked (no DB / brain-api).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth import api_key as ak
from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_api_key
from api.dependencies import get_db, get_redis
from api.repositories.dynamic_entity import EntityRecordError
from api.routers import v1 as v1_router
from api.routers.v1 import entities as v1_entities
from api.routers.v1 import knowledge as v1_knowledge
from api.routers.v1 import records as v1_records
from api.routers.v1 import reports as v1_reports
from api.routers.v1 import search as v1_search
from api.routers.v1 import workflows as v1_workflows
from api.services.api_rate_limit import RateLimitResult
from fastapi import FastAPI

_ALLOWED = RateLimitResult(allowed=True, limit=600, remaining=599, retry_after=0)


@pytest.fixture(autouse=True)
def _allow_rate_limit():  # noqa: ANN202
    """Default every test to an un-throttled limiter; the 429 test overrides this."""
    with patch.object(ak, "check_rate_limit", AsyncMock(return_value=_ALLOWED)):
        yield


def _principal(scopes: set[str]) -> ApiKeyPrincipal:
    return ApiKeyPrincipal(api_key_id=uuid.uuid4(), org_id=uuid.uuid4(), scopes=frozenset(scopes), name="k")


def _app(scopes: set[str]) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_router.router, prefix="/api/v1")
    app.dependency_overrides[require_api_key] = lambda: _principal(scopes)
    app.dependency_overrides[get_apikey_tenant_db] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_redis] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# (method, path, minimal-valid-body, the scope the endpoint should require)
_ENDPOINTS: list[tuple[str, str, dict | None, str]] = [
    ("GET", "/api/v1/entities", None, "entities:read"),
    ("GET", f"/api/v1/entities/{uuid.uuid4()}/records", None, "records:read"),
    ("POST", "/api/v1/entities/thing/records", {"x": 1}, "records:write"),
    ("GET", "/api/v1/reports", None, "reports:read"),
    ("POST", f"/api/v1/reports/{uuid.uuid4()}/run", {}, "reports:run"),
    ("GET", "/api/v1/workflows", None, "workflows:read"),
    ("POST", f"/api/v1/workflows/{uuid.uuid4()}/run", {}, "workflows:run"),
    ("POST", "/api/v1/search", {"query": "hello"}, "search:read"),
    ("GET", "/api/v1/knowledge/folders", None, "knowledge:read"),
]


class TestScopeGate:
    @pytest.mark.parametrize(("method", "path", "body", "scope"), _ENDPOINTS)
    async def test_missing_scope_is_403(self, method: str, path: str, body: dict | None, scope: str) -> None:
        # A key with NO scopes must be refused by every endpoint (proves each one
        # is scope-gated, not just the auth dependency in isolation).
        async with _client(_app(set())) as client:
            resp = await client.request(method, path, json=body)
        assert resp.status_code == 403, f"{method} {path} was not scope-gated"

    async def test_wrong_scope_does_not_satisfy_another(self) -> None:
        # Holding records:read must not grant records:write (verb/scope pairing).
        async with _client(_app({"records:read"})) as client:
            resp = await client.post("/api/v1/entities/thing/records", json={"x": 1})
        assert resp.status_code == 403


class TestRateLimit:
    async def test_429_on_real_route_carries_headers(self) -> None:
        blocked = RateLimitResult(allowed=False, limit=600, remaining=0, retry_after=42)
        with patch.object(ak, "check_rate_limit", AsyncMock(return_value=blocked)):
            async with _client(_app({"reports:read"})) as client:
                resp = await client.get("/api/v1/reports")
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "42"
        assert resp.headers["X-RateLimit-Remaining"] == "0"


class TestRecordsRouter:
    async def test_create_maps_entity_error_to_400(self) -> None:
        repo = MagicMock()
        repo.create = AsyncMock(side_effect=EntityRecordError("bad payload"))
        repo.last_change_event = None
        with (
            patch.object(v1_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))),
            patch.object(v1_records, "dispatch_inline_workflows", AsyncMock()),
        ):
            async with _client(_app({"records:write"})) as client:
                resp = await client.post("/api/v1/entities/thing/records", json={"x": 1})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "bad payload"

    async def test_get_missing_record_is_404(self) -> None:
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        with patch.object(v1_records, "build_record_repo", AsyncMock(return_value=(repo, MagicMock()))):
            async with _client(_app({"records:read"})) as client:
                resp = await client.get(f"/api/v1/entities/thing/records/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestWorkflowsRouter:
    async def test_run_no_published_version_is_409(self) -> None:
        wf = SimpleNamespace(active_version_id=None, entity_definition_id=uuid.uuid4())
        repo = MagicMock()
        repo.get = AsyncMock(return_value=wf)
        with patch.object(v1_workflows, "WorkflowRepository", return_value=repo):
            async with _client(_app({"workflows:run"})) as client:
                resp = await client.post(f"/api/v1/workflows/{uuid.uuid4()}/run", json={})
        assert resp.status_code == 409

    async def test_run_missing_workflow_is_404(self) -> None:
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        with patch.object(v1_workflows, "WorkflowRepository", return_value=repo):
            async with _client(_app({"workflows:run"})) as client:
                resp = await client.post(f"/api/v1/workflows/{uuid.uuid4()}/run", json={})
        assert resp.status_code == 404


class TestKnowledgeRouter:
    async def test_get_document_missing_is_404(self) -> None:
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        with patch.object(v1_knowledge, "DocumentRepository", return_value=repo):
            async with _client(_app({"knowledge:read"})) as client:
                resp = await client.get(f"/api/v1/knowledge/documents/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestSearchRouter:
    async def test_search_uses_org_wide_access_keys(self) -> None:
        # A service key must query brain-api with access_keys=None (org-wide) — the
        # security contract that it is not filtered by per-user permission masks.
        client_mock = MagicMock()
        client_mock.vector_search = AsyncMock(return_value={"hits": [], "total": 0})
        with patch.object(v1_search, "BrainAPIClient", return_value=client_mock):
            async with _client(_app({"search:read"})) as http:
                resp = await http.post("/api/v1/search", json={"query": "hello"})
        assert resp.status_code == 200
        assert client_mock.vector_search.await_args.kwargs["access_keys"] is None


class TestReportsRouter:
    async def test_list_reports_happy_path(self) -> None:
        svc = MagicMock()
        svc.list_reports = AsyncMock(return_value=[])
        with patch.object(v1_reports, "ReportService", return_value=svc):
            async with _client(_app({"reports:read"})) as client:
                resp = await client.get("/api/v1/reports")
        assert resp.status_code == 200


class TestEntitiesRouter:
    async def test_list_entities_happy_path(self) -> None:
        repo = MagicMock()
        repo.list_all = AsyncMock(return_value=([], 0))
        with patch.object(v1_entities, "EntityDefinitionRepository", return_value=repo):
            async with _client(_app({"entities:read"})) as client:
                resp = await client.get("/api/v1/entities")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
