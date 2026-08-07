"""Org-admin home-view settings (``PATCH /api/orgs/{org_id}/settings``).

The home view moved off the site-admin org editor: it points at a view the org
authored, so an org admin owns it. These tests pin that boundary — the org-admin
gate, the "the view must be ours" check that stands in for the missing FK, the
path-vs-header agreement, and the fact that the site-admin endpoint no longer
accepts the field at all. Repositories are mocked, so no database is required
(mirrors test_api_keys_router.py).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from api.auth.dependencies import OrgContext, get_current_user, require_org_admin
from api.dependencies import get_db
from api.routers import orgs as orgs_router
from api.schemas.org import OrgUpdate
from fastapi import FastAPI, HTTPException, status

ORG_ID = uuid.uuid4()
VIEW_ID = uuid.uuid4()


def _org(**over: object) -> SimpleNamespace:
    base = {
        "id": ORG_ID,
        "name": "Acme",
        "description": None,
        "use_knowledge_graph": True,
        "home_view_id": None,
        "default_llm_model": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(org_id: uuid.UUID = ORG_ID) -> OrgContext:
    return OrgContext(user=MagicMock(), org_id=org_id, membership=MagicMock(), is_org_admin=True)


def _deny_admin() -> None:
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="org admin required")


def _app(*, admin_ok: bool = True, ctx_org_id: uuid.UUID = ORG_ID) -> FastAPI:
    app = FastAPI()
    app.include_router(orgs_router.router, prefix="/api/orgs")
    app.dependency_overrides[require_org_admin] = (lambda: _ctx(ctx_org_id)) if admin_ok else _deny_admin
    app.dependency_overrides[get_current_user] = MagicMock
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _repos(org: SimpleNamespace | None, *, view_found: bool = True) -> tuple[MagicMock, MagicMock]:
    """Patched OrgRepository / ViewRepository doubles for the router module."""
    org_repo = MagicMock()
    org_repo.get = AsyncMock(return_value=org)

    async def _set_home_view(target: SimpleNamespace, view_id: uuid.UUID | None) -> SimpleNamespace:
        target.home_view_id = view_id
        return target

    org_repo.set_home_view = AsyncMock(side_effect=_set_home_view)

    view_repo = MagicMock()
    view_repo.get = AsyncMock(return_value=SimpleNamespace(id=VIEW_ID) if view_found else None)
    return org_repo, view_repo


class TestOrgAdminGate:
    async def test_non_admin_is_rejected(self) -> None:
        async with _client(_app(admin_ok=False)) as client:
            resp = await client.patch(f"/api/orgs/{ORG_ID}/settings", json={"home_view_id": str(VIEW_ID)})
        assert resp.status_code == 403

    async def test_path_org_must_match_the_active_org(self) -> None:
        """A stale tab must not write settings to the org it isn't showing."""
        async with _client(_app(ctx_org_id=uuid.uuid4())) as client:
            resp = await client.patch(f"/api/orgs/{ORG_ID}/settings", json={"home_view_id": str(VIEW_ID)})
        assert resp.status_code == 403


class TestSetAndClear:
    async def test_sets_the_home_view(self) -> None:
        org = _org()
        org_repo, view_repo = _repos(org)
        with (
            patch.object(orgs_router, "OrgRepository", return_value=org_repo),
            patch.object(orgs_router, "ViewRepository", return_value=view_repo),
        ):
            async with _client(_app()) as client:
                resp = await client.patch(f"/api/orgs/{ORG_ID}/settings", json={"home_view_id": str(VIEW_ID)})
        assert resp.status_code == 200
        assert resp.json()["home_view_id"] == str(VIEW_ID)
        assert org.home_view_id == VIEW_ID

    async def test_null_clears_the_home_view(self) -> None:
        """Replacement semantics: no sentinel UUID, an explicit null just clears."""
        org = _org(home_view_id=VIEW_ID)
        org_repo, view_repo = _repos(org)
        with (
            patch.object(orgs_router, "OrgRepository", return_value=org_repo),
            patch.object(orgs_router, "ViewRepository", return_value=view_repo),
        ):
            async with _client(_app()) as client:
                resp = await client.patch(f"/api/orgs/{ORG_ID}/settings", json={"home_view_id": None})
        assert resp.status_code == 200
        assert resp.json()["home_view_id"] is None
        assert org.home_view_id is None
        view_repo.get.assert_not_awaited()

    async def test_foreign_view_is_rejected(self) -> None:
        """orgs.home_view_id has no FK — the ownership check is the only guard."""
        org = _org()
        org_repo, view_repo = _repos(org, view_found=False)
        with (
            patch.object(orgs_router, "OrgRepository", return_value=org_repo),
            patch.object(orgs_router, "ViewRepository", return_value=view_repo),
        ):
            async with _client(_app()) as client:
                resp = await client.patch(f"/api/orgs/{ORG_ID}/settings", json={"home_view_id": str(uuid.uuid4())})
        assert resp.status_code == 422
        org_repo.set_home_view.assert_not_awaited()
        assert org.home_view_id is None

    async def test_missing_org_is_404(self) -> None:
        org_repo, view_repo = _repos(None)
        with (
            patch.object(orgs_router, "OrgRepository", return_value=org_repo),
            patch.object(orgs_router, "ViewRepository", return_value=view_repo),
        ):
            async with _client(_app()) as client:
                resp = await client.patch(f"/api/orgs/{ORG_ID}/settings", json={"home_view_id": None})
        assert resp.status_code == 404


class TestSiteAdminEndpointNoLongerAcceptsIt:
    def test_org_update_schema_has_no_home_view_field(self) -> None:
        assert "home_view_id" not in OrgUpdate.model_fields

    def test_home_view_in_a_site_admin_patch_is_ignored(self) -> None:
        # Pydantic drops unknown keys by default, so an old client's payload
        # silently no-ops rather than writing through the site-admin path.
        assert OrgUpdate(**{"name": "Acme", "home_view_id": str(VIEW_ID)}).name == "Acme"
