"""The runtime render endpoint accepts a view *slug* as well as a UUID.

Row-link templates and docs address the course player as
``/views/course_play/view?record_id={id}`` — a human-readable link an org admin
can author without pasting internal ids. The path segment therefore resolves as
UUID-first, slug-fallback (``ViewService.get_view_by_ref``). Admin CRUD stays
UUID-only: only the member-facing render surface gains the alias.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from api.auth.dependencies import OrgContext, require_org_access
from api.dependencies import get_tenant_db
from api.routers import views
from api.schemas.form import FormConfig, FormRenderRead
from api.services.form_service import FormNotFoundError
from api.services.view_service import ViewService
from fastapi import FastAPI

# ------------------------------------------------------------------ #
# Service: get_view_by_ref
# ------------------------------------------------------------------ #


def _service() -> ViewService:
    svc = ViewService.__new__(ViewService)
    svc._views = MagicMock()
    return svc


class TestGetViewByRef:
    async def test_uuid_ref_resolves_by_id(self) -> None:
        svc = _service()
        view = MagicMock()
        svc._views.get = AsyncMock(return_value=view)
        view_id = uuid.uuid4()

        assert await svc.get_view_by_ref(str(view_id)) is view
        svc._views.get.assert_awaited_once_with(view_id)
        svc._views.get_by_slug.assert_not_called()

    async def test_non_uuid_ref_resolves_by_slug(self) -> None:
        svc = _service()
        view = MagicMock()
        svc._views.get_by_slug = AsyncMock(return_value=view)

        assert await svc.get_view_by_ref("course_play") is view
        svc._views.get_by_slug.assert_awaited_once_with("course_play")
        svc._views.get.assert_not_called()

    async def test_unknown_slug_raises_not_found(self) -> None:
        svc = _service()
        svc._views.get_by_slug = AsyncMock(return_value=None)

        with pytest.raises(FormNotFoundError):
            await svc.get_view_by_ref("no_such_view")

    async def test_unknown_uuid_raises_not_found(self) -> None:
        svc = _service()
        svc._views.get = AsyncMock(return_value=None)

        with pytest.raises(FormNotFoundError):
            await svc.get_view_by_ref(str(uuid.uuid4()))


# ------------------------------------------------------------------ #
# Router: GET /views/{view_ref}/render
# ------------------------------------------------------------------ #


def _ctx() -> OrgContext:
    user = MagicMock()
    user.email = "member@example.com"
    return OrgContext(user=user, org_id=uuid.uuid4(), membership=MagicMock(), is_org_admin=False)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(views.router, prefix="/api/views")
    app.dependency_overrides[require_org_access] = _ctx
    app.dependency_overrides[get_tenant_db] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _render_service(view_id: uuid.UUID) -> MagicMock:
    service = MagicMock()
    service.render_by_ref = AsyncMock(
        return_value=FormRenderRead(
            form_id=view_id,
            form_name="Course Player",
            description=None,
            status="editable",
            root_entity_id=None,
            config=FormConfig(elements=[]),
            catalog=[],
            relationships=[],
            values={},
            related={},
        )
    )
    return service


class TestRenderBySlug:
    async def test_slug_renders_the_resolved_view(self) -> None:
        view_id = uuid.uuid4()
        record_id = uuid.uuid4()
        service = _render_service(view_id)
        with patch.object(views, "ViewService", return_value=service):
            async with _client(_app()) as client:
                resp = await client.get(f"/api/views/course_play/render?record_id={record_id}")

        assert resp.status_code == 200
        assert service.render_by_ref.await_args.args == ("course_play", record_id)

    async def test_uuid_still_renders(self) -> None:
        view_id = uuid.uuid4()
        service = _render_service(view_id)
        with patch.object(views, "ViewService", return_value=service):
            async with _client(_app()) as client:
                resp = await client.get(f"/api/views/{view_id}/render")

        assert resp.status_code == 200
        assert service.render_by_ref.await_args.args == (str(view_id), None)

    async def test_unknown_slug_is_404(self) -> None:
        service = MagicMock()
        service.render_by_ref = AsyncMock(side_effect=FormNotFoundError("view not found"))
        with patch.object(views, "ViewService", return_value=service):
            async with _client(_app()) as client:
                resp = await client.get("/api/views/no_such_view/render")

        assert resp.status_code == 404

    async def test_malformed_record_id_is_still_422(self) -> None:
        service = _render_service(uuid.uuid4())
        with patch.object(views, "ViewService", return_value=service):
            async with _client(_app()) as client:
                resp = await client.get("/api/views/course_play/render?record_id=not-a-uuid")

        assert resp.status_code == 422
        service.render_by_ref.assert_not_called()

    async def test_me_sentinel_still_binds_current_user(self) -> None:
        view_id = uuid.uuid4()
        service = _render_service(view_id)
        with patch.object(views, "ViewService", return_value=service):
            async with _client(_app()) as client:
                resp = await client.get("/api/views/course_play/render?record_id=me")

        assert resp.status_code == 200
        assert service.render_by_ref.await_args.kwargs["current_user_email"] == "member@example.com"
