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
from api.routers.v1 import agents as v1_agents
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


def _scoped_principal(
    scopes: set[str],
    masks: tuple[int, ...] = (0, 2_097_152),
    folder_ids: frozenset[uuid.UUID] | None = None,
) -> ApiKeyPrincipal:
    """A scoped key: carries its own assignments' resolved masks (and folders)."""
    return ApiKeyPrincipal(
        api_key_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        scopes=frozenset(scopes),
        name="k",
        access_mode="scoped",
        masks=masks,
        folder_ids=folder_ids,
    )


def _app(scopes: set[str], principal: ApiKeyPrincipal | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_router.router, prefix="/api/v1")
    resolved = principal or _principal(scopes)
    app.dependency_overrides[require_api_key] = lambda: resolved
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
    (
        "POST",
        "/api/v1/knowledge/documents",
        {"folder_id": str(uuid.uuid4()), "title": "t", "content": "hello"},
        "knowledge:write",
    ),
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
        # The per-IP pre-auth throttle runs first: let it pass so the per-key
        # limiter is the one that 429s (and must carry the X-RateLimit-* headers).
        ip_ok = RateLimitResult(allowed=True, limit=1200, remaining=1199, retry_after=0)
        key_blocked = RateLimitResult(allowed=False, limit=600, remaining=0, retry_after=42)

        async def by_key(_redis: object, key: str, **_kw: object) -> RateLimitResult:
            return ip_ok if key.startswith("ip:") else key_blocked

        with patch.object(ak, "check_rate_limit", AsyncMock(side_effect=by_key)):
            async with _client(_app({"reports:read"})) as client:
                resp = await client.get("/api/v1/reports")
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "42"
        assert resp.headers["X-RateLimit-Remaining"] == "0"

    async def test_ip_throttle_429s_before_key_resolution(self) -> None:
        # When the per-IP throttle blocks, the 429 fires pre-auth: no per-key
        # X-RateLimit-* headers, just Retry-After.
        ip_blocked = RateLimitResult(allowed=False, limit=1200, remaining=0, retry_after=9)
        with patch.object(ak, "check_rate_limit", AsyncMock(return_value=ip_blocked)):
            async with _client(_app({"reports:read"})) as client:
                resp = await client.get("/api/v1/reports")
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "9"
        assert "X-RateLimit-Remaining" not in resp.headers


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


class TestScopedKeySearch:
    """A scoped key searches with its own assignments' masks, never org-wide."""

    async def test_search_passes_the_key_masks(self) -> None:
        client_mock = MagicMock()
        client_mock.vector_search = AsyncMock(return_value={"hits": [], "total": 0})
        principal = _scoped_principal({"search:read"}, masks=(0, 2_097_152))
        with patch.object(v1_search, "BrainAPIClient", return_value=client_mock):
            async with _client(_app(set(), principal)) as http:
                resp = await http.post("/api/v1/search", json={"query": "hello"})
        assert resp.status_code == 200
        assert client_mock.vector_search.await_args.kwargs["access_keys"] == [0, 2_097_152]

    async def test_chat_passes_the_key_masks(self) -> None:
        client_mock = MagicMock()
        client_mock.vector_chat = AsyncMock(return_value={"answer": "", "sources": [], "graph_context": []})
        principal = _scoped_principal({"search:read"}, masks=(0, 4_194_304))
        with (
            patch.object(v1_search, "BrainAPIClient", return_value=client_mock),
            patch.object(v1_search, "org_default_llm_model", AsyncMock(return_value=None)),
        ):
            async with _client(_app(set(), principal)) as http:
                resp = await http.post("/api/v1/search/chat", json={"query": "hello"})
        assert resp.status_code == 200
        assert client_mock.vector_chat.await_args.kwargs["access_keys"] == [0, 4_194_304]

    async def test_scoped_key_without_masks_fails_closed(self) -> None:
        """An empty mask list reaches brain-api as "no filter". A scoped key must
        never get that far with nothing to filter by — refuse instead."""
        client_mock = MagicMock()
        client_mock.vector_search = AsyncMock(return_value={"hits": [], "total": 0})
        principal = _scoped_principal({"search:read"}, masks=())
        with patch.object(v1_search, "BrainAPIClient", return_value=client_mock):
            async with _client(_app(set(), principal)) as http:
                resp = await http.post("/api/v1/search", json={"query": "hello"})
        assert resp.status_code == 403
        client_mock.vector_search.assert_not_awaited()

    @pytest.mark.parametrize("path", ["/api/v1/search", "/api/v1/search/chat"])
    async def test_folder_limited_key_always_sends_its_folders(self, path: str) -> None:
        f1, f2 = uuid.uuid4(), uuid.uuid4()
        client_mock = MagicMock()
        client_mock.vector_search = AsyncMock(return_value={"hits": [], "total": 0})
        client_mock.vector_chat = AsyncMock(return_value={"answer": "", "sources": [], "graph_context": []})
        principal = _scoped_principal({"search:read"}, folder_ids=frozenset({f1, f2}))
        with (
            patch.object(v1_search, "BrainAPIClient", return_value=client_mock),
            patch.object(v1_search, "org_default_llm_model", AsyncMock(return_value=None)),
        ):
            async with _client(_app(set(), principal)) as http:
                resp = await http.post(path, json={"query": "hello"})
        assert resp.status_code == 200
        called = client_mock.vector_search if path.endswith("search") else client_mock.vector_chat
        assert sorted(called.await_args.kwargs["folder_tags"]) == sorted([f"folder:{f1}", f"folder:{f2}"])

    async def test_folder_outside_the_key_is_404(self) -> None:
        client_mock = MagicMock()
        client_mock.vector_search = AsyncMock(return_value={"hits": [], "total": 0})
        principal = _scoped_principal({"search:read"}, folder_ids=frozenset({uuid.uuid4()}))
        with patch.object(v1_search, "BrainAPIClient", return_value=client_mock):
            async with _client(_app(set(), principal)) as http:
                resp = await http.post("/api/v1/search", json={"query": "q", "folder_ids": [str(uuid.uuid4())]})
        assert resp.status_code == 404
        client_mock.vector_search.assert_not_awaited()

    @pytest.mark.parametrize("path", ["/api/v1/search", "/api/v1/search/chat"])
    async def test_empty_folder_set_never_reaches_brain(self, path: str) -> None:
        client_mock = MagicMock()
        client_mock.vector_search = AsyncMock(return_value={"hits": [], "total": 0})
        client_mock.vector_chat = AsyncMock(return_value={"answer": "", "sources": [], "graph_context": []})
        principal = _scoped_principal({"search:read"}, folder_ids=frozenset())
        with patch.object(v1_search, "BrainAPIClient", return_value=client_mock):
            async with _client(_app(set(), principal)) as http:
                resp = await http.post(path, json={"query": "q"})
        assert resp.status_code == 200
        client_mock.vector_search.assert_not_awaited()
        client_mock.vector_chat.assert_not_awaited()


class TestScopedKeyAgentPaths:
    """Agentic work started by any key has no actor (a key is not a person); a
    scoped key sees only the runs and work orders it created."""

    async def test_scoped_agent_run_has_no_actor_and_carries_the_key(self) -> None:
        agent = SimpleNamespace(id=uuid.uuid4(), enabled=True, provider="openai", model="m")
        agent_repo = MagicMock()
        agent_repo.get = AsyncMock(return_value=agent)
        run_repo = MagicMock()
        run_repo.create_run = AsyncMock(side_effect=RuntimeError("stop after capture"))
        principal = _scoped_principal({"agents:run"})
        with (
            patch.object(v1_agents, "AgentRepository", return_value=agent_repo),
            patch.object(v1_agents, "AgentRunRepository", return_value=run_repo),
        ):
            async with _client(_app(set(), principal)) as http:
                with pytest.raises(RuntimeError):
                    await http.post(f"/api/v1/agents/{agent.id}/run", json={"task": "x"})
        assert run_repo.create_run.await_args.kwargs["actor_user_id"] is None
        assert run_repo.create_run.await_args.kwargs["via_api_key"] is True
        assert run_repo.create_run.await_args.kwargs["api_key_id"] == principal.api_key_id

    async def test_org_key_agent_run_has_no_actor(self) -> None:
        agent = SimpleNamespace(id=uuid.uuid4(), enabled=True, provider="openai", model="m")
        agent_repo = MagicMock()
        agent_repo.get = AsyncMock(return_value=agent)
        run_repo = MagicMock()
        run_repo.create_run = AsyncMock(side_effect=RuntimeError("stop after capture"))
        with (
            patch.object(v1_agents, "AgentRepository", return_value=agent_repo),
            patch.object(v1_agents, "AgentRunRepository", return_value=run_repo),
        ):
            async with _client(_app({"agents:run"})) as http:
                with pytest.raises(RuntimeError):
                    await http.post(f"/api/v1/agents/{agent.id}/run", json={"task": "x"})
        assert run_repo.create_run.await_args.kwargs["actor_user_id"] is None
        assert run_repo.create_run.await_args.kwargs["via_api_key"] is True

    async def test_work_order_has_no_filing_profile(self) -> None:
        svc = MagicMock()
        svc.create_work_order = AsyncMock(side_effect=RuntimeError("stop after capture"))
        principal = _scoped_principal({"work_orders:write"})
        with patch.object(v1_agents, "WorkOrderService", return_value=svc):
            async with _client(_app(set(), principal)) as http:
                with pytest.raises(RuntimeError):
                    await http.post("/api/v1/work-orders", json={"title": "t"})
        assert svc.create_work_order.await_args.kwargs["created_by_profile_id"] is None
        assert svc.create_work_order.await_args.kwargs["via_api_key"] is True
        assert svc.create_work_order.await_args.kwargs["api_key_id"] == principal.api_key_id

    async def test_scoped_key_cannot_read_another_keys_run(self) -> None:
        run = SimpleNamespace(id=uuid.uuid4(), actor_user_id=None, api_key_id=uuid.uuid4())
        run_repo = MagicMock()
        run_repo.get_run = AsyncMock(return_value=run)
        principal = _scoped_principal({"agents:read"})
        with patch.object(v1_agents, "AgentRunRepository", return_value=run_repo):
            async with _client(_app(set(), principal)) as http:
                resp = await http.get(f"/api/v1/agents/runs/{run.id}")
        assert resp.status_code == 404

    async def test_scoped_key_lists_only_its_own_work_orders(self) -> None:
        svc = MagicMock()
        svc.list_work_orders = AsyncMock(return_value=[])
        principal = _scoped_principal({"work_orders:read"})
        with patch.object(v1_agents, "WorkOrderService", return_value=svc):
            async with _client(_app(set(), principal)) as http:
                resp = await http.get("/api/v1/work-orders")
        assert resp.status_code == 200
        assert svc.list_work_orders.await_args.kwargs["api_key_id"] == principal.api_key_id

    async def test_org_key_lists_every_work_order(self) -> None:
        svc = MagicMock()
        svc.list_work_orders = AsyncMock(return_value=[])
        with patch.object(v1_agents, "WorkOrderService", return_value=svc):
            async with _client(_app({"work_orders:read"})) as http:
                await http.get("/api/v1/work-orders")
        assert svc.list_work_orders.await_args.kwargs["api_key_id"] is None


class TestScopedKeysAreLimitedToScopeAwareRoutes:
    """Defense in depth: even if a scoped key somehow holds a scope whose routes
    ignore its assignments, the route refuses it."""

    @pytest.mark.parametrize(
        ("method", "path", "scope"),
        [
            ("GET", "/api/v1/entities", "entities:read"),
            ("GET", "/api/v1/reports", "reports:read"),
            ("POST", f"/api/v1/workflows/{uuid.uuid4()}/run", "workflows:run"),
            ("GET", "/api/v1/config/ping", "config:read"),
        ],
    )
    async def test_unaware_route_is_403_for_scoped_key(self, method: str, path: str, scope: str) -> None:
        principal = _scoped_principal({scope})
        async with _client(_app(set(), principal)) as http:
            resp = await http.request(method, path, json={} if method == "POST" else None)
        assert resp.status_code == 403


class TestKnowledgeWriteBoundary:
    """POST /api/v1/knowledge/documents validates everything before the write
    service runs. The service is patched so these never touch a DB."""

    @staticmethod
    def _svc() -> MagicMock:
        svc = MagicMock()
        svc.upsert = AsyncMock(
            return_value=SimpleNamespace(
                outcome="created",
                document=SimpleNamespace(
                    id=uuid.uuid4(),
                    document_key="k",
                    external_ref=None,
                    folder_id=uuid.uuid4(),
                    title="t",
                    processing_status="PENDING",
                    content_hash="0" * 64,
                ),
            )
        )
        return svc

    async def _post(self, svc: MagicMock, **kwargs: object) -> httpx.Response:
        with patch.object(v1_knowledge, "KnowledgeWriteService", return_value=svc):
            async with _client(_app({"knowledge:write"})) as http:
                return await http.post("/api/v1/knowledge/documents", **kwargs)

    async def test_json_happy_path_returns_201_and_status(self) -> None:
        svc = self._svc()
        folder = str(uuid.uuid4())
        resp = await self._post(
            svc,
            json={"folder_id": folder, "title": "Weekly", "content": "body", "external_ref": "proj-weekly-W41"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["outcome"] == "created"
        assert body["processing_status"] == "PENDING"
        sent = svc.upsert.await_args.args[0]
        assert str(sent.folder_id) == folder
        assert sent.content == b"body"
        assert sent.filename is None
        assert sent.external_ref == "proj-weekly-W41"

    async def test_unchanged_is_200(self) -> None:
        svc = self._svc()
        svc.upsert.return_value.outcome = "unchanged"
        resp = await self._post(svc, json={"folder_id": str(uuid.uuid4()), "title": "t", "content": "x"})
        assert resp.status_code == 200
        assert resp.json()["outcome"] == "unchanged"

    async def test_unsupported_content_type_is_415(self) -> None:
        svc = self._svc()
        resp = await self._post(svc, content=b"hello", headers={"Content-Type": "text/plain"})
        assert resp.status_code == 415
        svc.upsert.assert_not_awaited()

    @pytest.mark.parametrize(
        "body",
        [
            {"title": "t", "content": "x"},  # folder_id missing
            {"folder_id": "not-a-uuid", "title": "t", "content": "x"},
            {"folder_id": str(uuid.uuid4()), "content": "x"},  # title missing
            {"folder_id": str(uuid.uuid4()), "title": "t", "content": ""},  # empty content
            {"folder_id": str(uuid.uuid4()), "title": "t", "content": "x", "metadata": ["not", "an", "object"]},
            {"folder_id": str(uuid.uuid4()), "title": "t", "content": "x", "external_ref": "has spaces"},
            {"folder_id": str(uuid.uuid4()), "title": "t", "content": "x", "external_ref": "x" * 256},
            {"folder_id": str(uuid.uuid4()), "title": "t", "content": "x", "unexpected": 1},
            {"folder_id": str(uuid.uuid4()), "title": "t", "content": "x", "metadata": {"k": "v" * 20_000}},
        ],
    )
    async def test_json_validation_rejects(self, body: dict) -> None:
        svc = self._svc()
        resp = await self._post(svc, json=body)
        assert resp.status_code == 422, resp.text
        svc.upsert.assert_not_awaited()

    async def test_malformed_json_is_422(self) -> None:
        svc = self._svc()
        resp = await self._post(svc, content=b"{not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 422
        svc.upsert.assert_not_awaited()

    async def test_multipart_happy_path(self) -> None:
        svc = self._svc()
        folder = str(uuid.uuid4())
        resp = await self._post(
            svc,
            data={"folder_id": folder, "external_ref": "charter", "metadata": '{"source": "external-agent"}'},
            files={"file": ("charter.md", b"# Charter", "text/markdown")},
        )
        assert resp.status_code == 201, resp.text
        sent = svc.upsert.await_args.args[0]
        assert sent.filename == "charter.md"
        assert sent.content == b"# Charter"
        assert sent.title == "charter"  # defaults to the filename stem
        assert sent.metadata == {"source": "external-agent"}

    @pytest.mark.parametrize(
        ("data", "files", "code"),
        [
            ({"folder_id": "F"}, {"file": ("a.zip", b"PK..", "application/zip")}, 400),  # one doc per call
            ({"folder_id": "F"}, {"file": ("a.exe", b"MZ", "application/octet-stream")}, 400),
            ({"folder_id": "F"}, {"file": ("a.md", b"", "text/markdown")}, 400),  # empty
            ({}, {"file": ("a.md", b"x", "text/markdown")}, 422),  # folder_id missing
            ({"folder_id": "F", "metadata": "{bad"}, {"file": ("a.md", b"x", "text/markdown")}, 422),
            ({"folder_id": "F", "metadata": "[1]"}, {"file": ("a.md", b"x", "text/markdown")}, 422),
            ({"folder_id": "F", "external_ref": "a b"}, {"file": ("a.md", b"x", "text/markdown")}, 422),
            ({"folder_id": "nope"}, {"file": ("a.md", b"x", "text/markdown")}, 422),
        ],
    )
    async def test_multipart_validation_rejects(self, data: dict, files: dict, code: int) -> None:
        svc = self._svc()
        payload = {k: (str(uuid.uuid4()) if v == "F" else v) for k, v in data.items()}
        resp = await self._post(svc, data=payload, files=files)
        assert resp.status_code == code, resp.text
        svc.upsert.assert_not_awaited()

    async def test_oversized_file_is_413(self) -> None:
        svc = self._svc()
        settings = MagicMock(max_file_size_mb=1)
        with patch.object(v1_knowledge, "get_settings", return_value=settings):
            app = _app({"knowledge:write"})
            app.dependency_overrides[v1_knowledge.get_settings] = lambda: settings
            with patch.object(v1_knowledge, "KnowledgeWriteService", return_value=svc):
                async with _client(app) as http:
                    resp = await http.post(
                        "/api/v1/knowledge/documents",
                        data={"folder_id": str(uuid.uuid4())},
                        files={"file": ("big.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
                    )
        assert resp.status_code == 413
        svc.upsert.assert_not_awaited()

    async def test_oversized_json_content_is_413(self) -> None:
        svc = self._svc()
        settings = MagicMock(max_file_size_mb=1)
        app = _app({"knowledge:write"})
        app.dependency_overrides[v1_knowledge.get_settings] = lambda: settings
        with patch.object(v1_knowledge, "KnowledgeWriteService", return_value=svc):
            async with _client(app) as http:
                resp = await http.post(
                    "/api/v1/knowledge/documents",
                    json={"folder_id": str(uuid.uuid4()), "title": "t", "content": "x" * (1024 * 1024 + 1)},
                )
        assert resp.status_code == 413
        svc.upsert.assert_not_awaited()

    async def test_read_scope_cannot_write(self) -> None:
        svc = self._svc()
        with patch.object(v1_knowledge, "KnowledgeWriteService", return_value=svc):
            async with _client(_app({"knowledge:read"})) as http:
                resp = await http.post(
                    "/api/v1/knowledge/documents",
                    json={"folder_id": str(uuid.uuid4()), "title": "t", "content": "x"},
                )
        assert resp.status_code == 403
        svc.upsert.assert_not_awaited()


class TestKnowledgeWriteBoundaryHardening:
    @staticmethod
    def _svc() -> MagicMock:
        return TestKnowledgeWriteBoundary._svc()

    async def _post(self, svc: MagicMock, app: FastAPI | None = None, **kwargs: object) -> httpx.Response:
        with patch.object(v1_knowledge, "KnowledgeWriteService", return_value=svc):
            async with _client(app or _app({"knowledge:write"})) as http:
                return await http.post("/api/v1/knowledge/documents", **kwargs)

    @pytest.mark.parametrize(
        "over",
        [
            {"content": "a\x00b"},
            {"title": "t\x00"},
            {"metadata": {"k": "v\x00"}},
            {"metadata": {"access_keys": [0]}},
            {"metadata": {"tags": ["x"]}},
            {"metadata": {"document_key": "x"}},
            {"metadata": {"tenant_id": "x"}},
            {"metadata": {"nested": {"a": 1}}},
            {"metadata": {"list": [{"a": 1}]}},
            {"external_ref": "ref\n"},
        ],
    )
    async def test_json_rejects(self, over: dict) -> None:
        svc = self._svc()
        body = {"folder_id": str(uuid.uuid4()), "title": "t", "content": "x", **over}
        resp = await self._post(svc, json=body)
        assert resp.status_code == 422, resp.text
        svc.upsert.assert_not_awaited()

    async def test_json_scalar_and_scalar_list_metadata_accepted(self) -> None:
        svc = self._svc()
        meta = {"source": "agent", "run": 3, "ok": True, "none": None, "labels": ["a", "b"]}
        resp = await self._post(
            svc, json={"folder_id": str(uuid.uuid4()), "title": "t", "content": "x", "metadata": meta}
        )
        assert resp.status_code == 201, resp.text
        assert svc.upsert.await_args.args[0].metadata == meta

    async def test_deeply_nested_json_is_422_not_500(self) -> None:
        svc = self._svc()
        deep = "[" * 5000 + "]" * 5000
        raw = f'{{"folder_id": "{uuid.uuid4()}", "title": "t", "content": "x", "metadata": {deep}}}'
        resp = await self._post(svc, content=raw.encode(), headers={"Content-Type": "application/json"})
        assert resp.status_code == 422
        svc.upsert.assert_not_awaited()

    async def test_deeply_nested_multipart_metadata_is_422_not_500(self) -> None:
        svc = self._svc()
        resp = await self._post(
            svc,
            data={"folder_id": str(uuid.uuid4()), "metadata": "[" * 100_000 + "]" * 100_000},
            files={"file": ("a.md", b"x", "text/markdown")},
        )
        assert resp.status_code == 422
        svc.upsert.assert_not_awaited()

    async def test_multipart_reserved_metadata_rejected(self) -> None:
        svc = self._svc()
        resp = await self._post(
            svc,
            data={"folder_id": str(uuid.uuid4()), "metadata": '{"access_keys": [0]}'},
            files={"file": ("a.md", b"x", "text/markdown")},
        )
        assert resp.status_code == 422

    async def test_multipart_nul_in_title_rejected(self) -> None:
        svc = self._svc()
        resp = await self._post(
            svc,
            data={"folder_id": str(uuid.uuid4()), "title": "a\x00b"},
            files={"file": ("a.md", b"x", "text/markdown")},
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("weird name?*.md", "weird name__.md"),
            ("..hidden.md", "hidden.md"),
            ("dir\\b.md", "b.md"),  # a Windows path keeps only its last segment
            ("x" * 300 + ".pdf", "x" * 196 + ".pdf"),
        ],
    )
    async def test_filenames_are_sanitised_and_capped(self, name: str, expected: str) -> None:
        svc = self._svc()
        resp = await self._post(
            svc,
            data={"folder_id": str(uuid.uuid4())},
            files={"file": (name, b"x", "application/octet-stream")},
        )
        assert resp.status_code == 201, resp.text
        assert svc.upsert.await_args.args[0].filename == expected

    async def test_declared_oversized_multipart_is_413_before_parsing(self) -> None:
        svc = self._svc()
        settings = MagicMock(max_file_size_mb=1, api_key_document_writes_per_day=1000)
        app = _app({"knowledge:write"})
        app.dependency_overrides[v1_knowledge.get_settings] = lambda: settings
        with patch("starlette.requests.Request.form", side_effect=AssertionError("parsed an oversized body")):
            resp = await self._post(
                svc,
                app=app,
                content=b"x",
                headers={"Content-Type": "multipart/form-data; boundary=b", "Content-Length": str(3 * 1024 * 1024)},
            )
        assert resp.status_code == 413
        svc.upsert.assert_not_awaited()

    async def test_streamed_oversized_multipart_is_413(self) -> None:
        """No Content-Length (chunked): the byte counter stops it mid-stream."""
        svc = self._svc()
        settings = MagicMock(max_file_size_mb=1, api_key_document_writes_per_day=1000)
        app = _app({"knowledge:write"})
        app.dependency_overrides[v1_knowledge.get_settings] = lambda: settings
        boundary = "bnd"
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="folder_id"\r\n\r\n{uuid.uuid4()}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()

        async def body():  # noqa: ANN202
            yield head
            for _ in range(40):
                yield b"x" * 65_536  # 2.5 MiB total
            yield f"\r\n--{boundary}--\r\n".encode()

        resp = await self._post(
            svc, app=app, content=body(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
        )
        assert resp.status_code == 413
        svc.upsert.assert_not_awaited()

    async def test_daily_write_cap_is_429(self) -> None:
        svc = self._svc()
        blocked = RateLimitResult(allowed=False, limit=500, remaining=0, retry_after=3600)

        async def by_key(_redis: object, key: str, **_kw: object) -> RateLimitResult:
            return blocked if key.startswith("docwrite:") else _ALLOWED

        with patch.object(v1_knowledge, "peek_rate_limit", AsyncMock(side_effect=by_key)):
            resp = await self._post(svc, json={"folder_id": str(uuid.uuid4()), "title": "t", "content": "x"})
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "3600"
        svc.upsert.assert_not_awaited()


def _agent_row() -> SimpleNamespace:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="researcher",
        display_name="Researcher",
        description="Finds things",
        kind="operator",
        persona="SECRET PERSONA PROMPT",
        provider="openai",
        model="gpt-x",
        params={"temperature": 0.2},
        supervisor_id=None,
        avatar=None,
        accent=None,
        enabled=True,
        grants={"knowledge_scope": "org"},
        mcp_server_ids=[uuid.uuid4()],
        workflow_allowlist=[],
        workflow_invocable=[],
        created_at=now,
        updated_at=now,
    )


class TestV1AgentListByAccessMode:
    """A scoped key may list agents to start them, but not read how they are built:
    persona (the system prompt), params, grants and MCP servers stay org-key only."""

    _HIDDEN = ("persona", "params", "grants", "mcp_server_ids")

    async def _list(self, principal: ApiKeyPrincipal) -> list[dict]:
        repo = MagicMock(list_all=AsyncMock(return_value=[_agent_row()]))
        with patch.object(v1_agents, "AgentRepository", return_value=repo):
            async with _client(_app(set(), principal)) as http:
                resp = await http.get("/api/v1/agents")
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def test_scoped_key_gets_the_summary_only(self) -> None:
        (row,) = await self._list(_scoped_principal({"agents:read"}))
        for field in self._HIDDEN:
            assert field not in row
        assert row["name"] == "researcher" and row["description"] == "Finds things"
        assert row["enabled"] is True and row["kind"] == "operator"
        assert "SECRET" not in str(row)

    async def test_org_key_is_unchanged(self) -> None:
        (row,) = await self._list(_principal({"agents:read"}))
        assert row["persona"] == "SECRET PERSONA PROMPT"
        assert row["grants"] == {"knowledge_scope": "org"}
