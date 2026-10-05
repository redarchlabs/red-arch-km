"""Two ROOT folders may share a name; neither may inherit from or reach the other.

``UniqueConstraint(org_id, name, parent_id)`` does not stop two roots with the same
name (``parent_id`` is NULL for both), so their ``dot_path``s — and those of
same-named children — are identical. Anything that resolves the tree by
``dot_path`` (ancestor lookup, subtree walk, visibility map, cycle check, path
rewrite) can then pick up the other root's subtree or inherited permissions.
The tree is walked by ``parent_id``; ``dot_path`` is a display string only.

Tree under test (masks in brackets, * = own viewer / contributor config):

    Shared* [7] (contributors [70])     Shared (public, no config)
    └── Kids  (inherits 7)              └── Kids  (inherits nothing → public)
        └── docRestricted                   └── docPublic
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from api.models.document import Document, Folder
from api.models.org import Org
from api.repositories.folder import FolderRepository
from api.routers.folders import _collect_subtree_propagation
from api.services.folder_service import move_folder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_CFG = [{"role": "manager"}]  # opaque non-null marker; masks are set explicitly


async def _seed(session: AsyncSession) -> tuple[Org, dict[str, Any]]:
    org = Org(name=f"Twins-{uuid.uuid4().hex[:8]}", permission_number=1)
    session.add(org)
    await session.flush()
    await set_tenant(session, str(org.id))

    restricted = Folder(
        name="Shared",
        org_id=org.id,
        dot_path="Shared",
        viewer_permissions_config=_CFG,
        view_permission_masks=[7],
        contributor_permissions_config=_CFG,
        contributor_permission_masks=[70],
    )
    public = Folder(name="Shared", org_id=org.id, dot_path="Shared")
    session.add_all([restricted, public])
    await session.flush()
    restricted_kids = Folder(name="Kids", org_id=org.id, dot_path="Shared.Kids", parent_id=restricted.id)
    public_kids = Folder(name="Kids", org_id=org.id, dot_path="Shared.Kids", parent_id=public.id)
    session.add_all([restricted_kids, public_kids])
    await session.flush()
    doc_restricted = Document(title="docRestricted", org_id=org.id, folder_id=restricted_kids.id, text="r")
    doc_public = Document(title="docPublic", org_id=org.id, folder_id=public_kids.id, text="p")
    session.add_all([doc_restricted, doc_public])
    await session.flush()
    return org, {
        "restricted": restricted,
        "public": public,
        "restricted_kids": restricted_kids,
        "public_kids": public_kids,
        "doc_restricted": doc_restricted,
        "doc_public": doc_public,
    }


class TestInheritance:
    async def test_nearest_configured_ancestor_is_found_by_parent(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        repo = FolderRepository(session, org.id)
        assert await repo.nearest_configured_ancestor(t["public_kids"]) is None
        ancestor = await repo.nearest_configured_ancestor(t["restricted_kids"])
        assert ancestor is not None and ancestor.id == t["restricted"].id

    async def test_effective_view_masks_do_not_cross_roots(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        repo = FolderRepository(session, org.id)
        assert await repo.effective_view_masks(t["public_kids"]) == []
        assert await repo.effective_view_masks(t["restricted_kids"]) == [7]

    async def test_contributor_inheritance_does_not_cross_roots(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        repo = FolderRepository(session, org.id)
        assert await repo.nearest_contributor_configured_ancestor(t["public_kids"]) is None
        assert await repo.effective_contributor_masks(t["public_kids"]) == []
        assert await repo.effective_contributor_masks(t["restricted_kids"]) == [70]


class TestPropagation:
    async def test_descendants_stay_in_their_own_tree(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        repo = FolderRepository(session, org.id)
        assert {f.id for f in await repo.descendants(t["restricted"])} == {
            t["restricted"].id,
            t["restricted_kids"].id,
        }
        assert {f.id for f in await repo.descendants(t["public"])} == {t["public"].id, t["public_kids"].id}

    async def test_a_permission_change_rescopes_only_its_own_subtree(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        payloads = await _collect_subtree_propagation(session, org.id, t["restricted"])
        keys = {p["document_key"] for p in payloads}
        assert t["doc_restricted"].document_key in keys
        assert t["doc_public"].document_key not in keys  # the other root's document is untouched
        assert all(p["new_access_keys"] == [7] for p in payloads)

    async def test_the_public_twin_propagates_public(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        payloads = await _collect_subtree_propagation(session, org.id, t["public"])
        assert {p["document_key"] for p in payloads} == {t["doc_public"].document_key}
        assert payloads[0]["new_access_keys"] == []


class TestVisibility:
    async def test_a_non_member_sees_the_public_twin_only(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        visible, total = await FolderRepository(session, org.id).list_visible_to_masks(user_masks=[99])
        assert {f.id for f in visible} == {t["public"].id, t["public_kids"].id}
        assert total == 2

    async def test_a_member_sees_both(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        visible, _ = await FolderRepository(session, org.id).list_visible_to_masks(user_masks=[7])
        assert {f.id for f in visible} == {
            t["restricted"].id,
            t["restricted_kids"].id,
            t["public"].id,
            t["public_kids"].id,
        }


class TestRestructure:
    async def _paths(self, session: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, str]:
        rows = await session.execute(
            select(Folder).where(Folder.org_id == org_id).execution_options(populate_existing=True)
        )
        return {f.id: f.dot_path for f in rows.scalars()}

    async def test_renaming_a_root_rewrites_only_its_own_subtree(self, session: AsyncSession) -> None:
        org, t = await _seed(session)
        await FolderRepository(session, org.id).rename(t["restricted"], "Private")
        paths = await self._paths(session, org.id)
        assert paths[t["restricted"].id] == "Private"
        assert paths[t["restricted_kids"].id] == "Private.Kids"
        assert paths[t["public"].id] == "Shared"
        assert paths[t["public_kids"].id] == "Shared.Kids"

    async def test_a_root_may_move_under_its_twins_child(self, session: AsyncSession) -> None:
        # Not a cycle: the target is in the OTHER tree, though its path starts "Shared.".
        org, t = await _seed(session)
        await move_folder(session, org.id, t["restricted"], t["public_kids"].id)
        paths = await self._paths(session, org.id)
        assert paths[t["restricted"].id] == "Shared.Kids.Shared"
        assert paths[t["restricted_kids"].id] == "Shared.Kids.Shared.Kids"
        assert paths[t["public_kids"].id] == "Shared.Kids"  # the twin's child is not rewritten
