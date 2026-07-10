"""Unit tests for the reports router: the admin/member auth split, FormError→HTTP
status mapping, and request-shape validation. ReportService is mocked so no
database is required (mirrors test_entity_records_router.py)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.dependencies import get_tenant_db
from api.routers import reports
from api.services.form_service import FormConflictError, FormNotFoundError, FormValidationError
from fastapi import FastAPI, HTTPException, status


def _ctx() -> OrgContext:
    return OrgContext(user=MagicMock(), org_id=uuid.uuid4(), membership=MagicMock(), is_org_admin=True)


def _deny_admin() -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="org admin required")


def _app(*, admin_ok: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(reports.router, prefix="/api/reports")
    app.dependency_overrides[require_org_access] = _ctx
    app.dependency_overrides[require_org_admin] = _ctx if admin_ok else _deny_admin
    app.dependency_overrides[get_tenant_db] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _create_body() -> dict:
    return {
        "name": "R",
        "slug": "r",
        "entity_definition_id": str(uuid.uuid4()),
        "query": {"metrics": [{"op": "count", "alias": "count"}]},
        "viz": {"type": "bar", "series": []},
    }


class TestAdminGate:
    async def test_non_admin_cannot_create(self) -> None:
        async with _client(_app(admin_ok=False)) as client:
            resp = await client.post("/api/reports/", json=_create_body())
        assert resp.status_code == 403

    async def test_non_admin_cannot_delete(self) -> None:
        async with _client(_app(admin_ok=False)) as client:
            resp = await client.delete(f"/api/reports/{uuid.uuid4()}")
        assert resp.status_code == 403

    async def test_member_can_list_and_run(self) -> None:
        svc = MagicMock()
        svc.list_reports = AsyncMock(return_value=[])
        with patch.object(reports, "ReportService", return_value=svc):
            async with _client(_app(admin_ok=False)) as client:  # member (non-admin)
                resp = await client.get("/api/reports/")
        assert resp.status_code == 200


class TestErrorMapping:
    async def test_unknown_report_is_404(self) -> None:
        svc = MagicMock()
        svc.get_report = AsyncMock(side_effect=FormNotFoundError("report not found"))
        with patch.object(reports, "ReportService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.get(f"/api/reports/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_validation_error_is_400(self) -> None:
        svc = MagicMock()
        svc.create_report = AsyncMock(side_effect=FormValidationError("bad query"))
        with patch.object(reports, "ReportService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post("/api/reports/", json=_create_body())
        assert resp.status_code == 400
        assert resp.json()["detail"] == "bad query"

    async def test_slug_conflict_is_409(self) -> None:
        svc = MagicMock()
        svc.create_report = AsyncMock(side_effect=FormConflictError("slug exists"))
        with patch.object(reports, "ReportService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post("/api/reports/", json=_create_body())
        assert resp.status_code == 409


class TestRun:
    async def test_run_report_ok(self) -> None:
        svc = MagicMock()
        svc.run_report = AsyncMock(
            return_value={"group_by": [], "metrics": ["count"], "rows": [{"count": 3}], "row_count": 1}
        )
        with patch.object(reports, "ReportService", return_value=svc):
            async with _client(_app()) as client:
                resp = await client.post(f"/api/reports/{uuid.uuid4()}/run", json={})
        assert resp.status_code == 200
        assert resp.json()["rows"] == [{"count": 3}]

    async def test_adhoc_missing_entity_is_422(self) -> None:
        async with _client(_app()) as client:
            resp = await client.post("/api/reports/run", json={"query": {}})  # no entity_definition_id
        assert resp.status_code == 422
