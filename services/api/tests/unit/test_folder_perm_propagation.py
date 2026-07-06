"""Unit tests for PATCH /api/folders/{id} permission propagation.

When a folder's viewer permissions change, every document that inherits the
folder (NULL viewer config) must have its brain-api ``access_keys`` refreshed
via a metadata-update task, while documents with their own override are left
alone. The DB, mask resolution, and dispatch are stubbed so this runs without
Postgres or a broker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from api.auth.dependencies import CurrentUser, OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.routers import folders as folders_module
from api.routers.folders import router as folders_router
from fastapi import FastAPI

ORG_ID = uuid.uuid4()
FOLDER_ID = uuid.uuid4()


def _ctx() -> OrgContext:
    user = CurrentUser(
        sub="u", username="u", email="u@x.com", profile_id=uuid.uuid4(), is_site_admin=False
    )
    return OrgContext(user=user, org_id=ORG_ID, membership=MagicMock(), is_org_admin=True)


def _fake_folder() -> SimpleNamespace:
    return SimpleNamespace(
        id=FOLDER_ID,
        name="HR",
        description=None,
        parent_id=None,
        dot_path="HR",
        order=0,
        org_id=ORG_ID,
        created_at=datetime.now(UTC),
        viewer_permissions_config=None,
        contributor_permissions_config=None,
        view_permission_masks=[],
        contributor_permission_masks=[],
    )


def _doc(key: str, tags: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        document_key=key,
        title=f"doc-{key}",
        tags=[SimpleNamespace(name=t) for t in tags],
    )


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"dispatched": [], "inheriting": []}

    class _FolderRepo:
        def __init__(self, session: Any, org_id: uuid.UUID) -> None: ...
        async def get(self, _id: uuid.UUID) -> SimpleNamespace:
            return state["folder"]

        async def descendants(self, _folder: Any) -> list[Any]:
            # Single-folder subtree; recursion across a real tree is covered by
            # the integration tests.
            return [state["folder"]]

        async def effective_view_masks(self, folder: Any) -> list[int]:
            return list(folder.view_permission_masks or []) if folder else []

    class _DocRepo:
        def __init__(self, session: Any, org_id: uuid.UUID) -> None: ...
        async def list_inheriting_in_folder(self, _folder_id: uuid.UUID) -> list[Any]:
            return state["inheriting"]

    async def _fake_masks(_s: Any, _o: uuid.UUID, viewer: Any, _c: Any) -> tuple[list[int], list[int]]:
        # Deterministic: a non-empty viewer config resolves to [99]; else [].
        return ([99] if viewer else []), []

    def _dispatch(data: dict[str, Any]) -> str:
        state["dispatched"].append(data)
        return f"task-{len(state['dispatched'])}"

    monkeypatch.setattr(folders_module, "FolderRepository", _FolderRepo)
    monkeypatch.setattr(folders_module, "DocumentRepository", _DocRepo)
    monkeypatch.setattr(folders_module, "compute_folder_masks", _fake_masks)
    monkeypatch.setattr(folders_module, "dispatch_metadata_update", _dispatch)

    state["folder"] = _fake_folder()
    return state


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(folders_router, prefix="/api/folders")

    async def _fake_db() -> Any:
        yield AsyncMock()

    app.dependency_overrides[require_org_admin] = _ctx
    app.dependency_overrides[get_tenant_db] = _fake_db
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_viewer_perms_change_propagates_to_inheriting_docs(wiring: dict[str, Any]) -> None:
    wiring["inheriting"] = [_doc("k1", ["alpha"]), _doc("k2", [])]

    async with _client(_app()) as client:
        resp = await client.patch(
            f"/api/folders/{FOLDER_ID}",
            json={"viewer_permissions_config": [{"role": "manager"}]},
        )
    assert resp.status_code == 200

    dispatched = wiring["dispatched"]
    assert {d["document_key"] for d in dispatched} == {"k1", "k2"}
    for d in dispatched:
        assert d["new_access_keys"] == [99]  # the folder's freshly-resolved masks
        assert f"folder:{FOLDER_ID}" in d["new_tags"]
    # The doc's own tags are preserved alongside the synthetic folder tag.
    k1 = next(d for d in dispatched if d["document_key"] == "k1")
    assert "alpha" in k1["new_tags"]


async def test_contributor_only_change_does_not_propagate(wiring: dict[str, Any]) -> None:
    wiring["inheriting"] = [_doc("k1", [])]

    async with _client(_app()) as client:
        resp = await client.patch(
            f"/api/folders/{FOLDER_ID}",
            json={"contributor_permissions_config": [{"role": "editor"}]},
        )
    assert resp.status_code == 200
    # Contributor config doesn't affect retrieval entitlement → no propagation.
    assert wiring["dispatched"] == []


async def test_rename_only_does_not_propagate(wiring: dict[str, Any]) -> None:
    wiring["inheriting"] = [_doc("k1", [])]

    async with _client(_app()) as client:
        resp = await client.patch(f"/api/folders/{FOLDER_ID}", json={"description": "notes"})
    assert resp.status_code == 200
    assert wiring["dispatched"] == []


async def test_viewer_change_with_no_inheriting_docs_dispatches_nothing(wiring: dict[str, Any]) -> None:
    wiring["inheriting"] = []

    async with _client(_app()) as client:
        resp = await client.patch(
            f"/api/folders/{FOLDER_ID}",
            json={"viewer_permissions_config": [{"role": "manager"}]},
        )
    assert resp.status_code == 200
    assert wiring["dispatched"] == []
