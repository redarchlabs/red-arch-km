"""Integration tests for recursive folder-permission inheritance.

Documents inherit their folder's entitlement, and folders with no viewer config
of their own inherit the nearest configured ancestor. A folder that defines its
own config is an inheritance boundary: it and its subtree are unaffected by an
ancestor's change. Exercised against real PostgreSQL (RLS) so the dot_path
ancestor/descendant queries are covered end to end.

Tree under test (masks in brackets, * = own viewer config):

    A* [7]
    └── B            (inherits A → 7)
        ├── C* [9]   (own config; a boundary)
        │   └── D    (inherits C → 9)
        └── (docB, docOverride[42])
"""

from __future__ import annotations

import uuid

import pytest
from api.models.document import Document, Folder
from api.models.org import Org
from api.repositories.folder import FolderRepository
from api.routers.folders import _collect_subtree_propagation
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

_CFG = [{"role": "manager"}]  # opaque non-null marker; masks are set explicitly


async def _seed_tree(session: AsyncSession) -> tuple[Org, dict[str, Folder], dict[str, Document]]:
    org = Org(name=f"Inherit-{uuid.uuid4().hex[:8]}", permission_number=1)
    session.add(org)
    await session.flush()
    await set_tenant(session, str(org.id))

    a = Folder(name="A", org_id=org.id, dot_path="A", viewer_permissions_config=_CFG, view_permission_masks=[7])
    b = Folder(name="B", org_id=org.id, dot_path="A.B", parent_id=None)
    session.add_all([a, b])
    await session.flush()
    b.parent_id = a.id
    c = Folder(
        name="C", org_id=org.id, dot_path="A.B.C", parent_id=b.id,
        viewer_permissions_config=_CFG, view_permission_masks=[9],
    )
    session.add(c)
    await session.flush()
    d = Folder(name="D", org_id=org.id, dot_path="A.B.C.D", parent_id=c.id)
    session.add(d)
    await session.flush()

    docs = {
        "a": Document(title="docA", org_id=org.id, folder_id=a.id, text="a"),
        "b": Document(title="docB", org_id=org.id, folder_id=b.id, text="b"),
        "c": Document(title="docC", org_id=org.id, folder_id=c.id, text="c"),
        "d": Document(title="docD", org_id=org.id, folder_id=d.id, text="d"),
        "override": Document(
            title="docOverride", org_id=org.id, folder_id=b.id, text="o",
            viewer_permissions_config=_CFG, view_permission_masks=[42],
        ),
    }
    session.add_all(list(docs.values()))
    await session.flush()
    return org, {"A": a, "B": b, "C": c, "D": d}, docs


class TestEffectiveMasks:
    async def test_own_config_folder_uses_own_masks(self, session: AsyncSession) -> None:
        org, folders, _ = await _seed_tree(session)
        repo = FolderRepository(session, org.id)
        assert await repo.effective_view_masks(folders["A"]) == [7]
        assert await repo.effective_view_masks(folders["C"]) == [9]

    async def test_unconfigured_folder_inherits_nearest_ancestor(self, session: AsyncSession) -> None:
        org, folders, _ = await _seed_tree(session)
        repo = FolderRepository(session, org.id)
        # B has no config → inherits A. D has no config but sits under C → inherits C.
        assert await repo.effective_view_masks(folders["B"]) == [7]
        assert await repo.effective_view_masks(folders["D"]) == [9]

    async def test_nearest_configured_ancestor(self, session: AsyncSession) -> None:
        org, folders, _ = await _seed_tree(session)
        repo = FolderRepository(session, org.id)
        assert (await repo.nearest_configured_ancestor(folders["B"])).id == folders["A"].id
        assert (await repo.nearest_configured_ancestor(folders["D"])).id == folders["C"].id
        assert await repo.nearest_configured_ancestor(folders["A"]) is None

    async def test_root_folder_without_config_is_public(self, session: AsyncSession) -> None:
        org = Org(name=f"Pub-{uuid.uuid4().hex[:8]}", permission_number=1)
        session.add(org)
        await session.flush()
        await set_tenant(session, str(org.id))
        root = Folder(name="Root", org_id=org.id, dot_path="Root")
        session.add(root)
        await session.flush()
        assert await FolderRepository(session, org.id).effective_view_masks(root) == []


class TestSubtreePropagation:
    async def test_change_propagates_to_inheriting_subtree_only(self, session: AsyncSession) -> None:
        org, folders, docs = await _seed_tree(session)

        payloads = await _collect_subtree_propagation(session, org.id, folders["A"])

        by_key = {p["document_key"]: p for p in payloads}
        # docA (in A) and docB (in B, inherits A) are re-scoped to A's masks.
        # docC/docD sit under the C boundary; docOverride has its own config.
        assert set(by_key) == {docs["a"].document_key, docs["b"].document_key}
        for p in by_key.values():
            assert p["new_access_keys"] == [7]

    async def test_boundary_folder_change_scoped_to_its_subtree(self, session: AsyncSession) -> None:
        org, folders, docs = await _seed_tree(session)

        payloads = await _collect_subtree_propagation(session, org.id, folders["C"])

        by_key = {p["document_key"]: p for p in payloads}
        # Changing C reaches docC (in C) and docD (in D, inherits C) — not upward.
        assert set(by_key) == {docs["c"].document_key, docs["d"].document_key}
        for p in by_key.values():
            assert p["new_access_keys"] == [9]

    async def test_tag_reflects_document_own_folder(self, session: AsyncSession) -> None:
        org, folders, docs = await _seed_tree(session)
        payloads = await _collect_subtree_propagation(session, org.id, folders["A"])
        by_key = {p["document_key"]: p for p in payloads}
        # docB's synthetic folder tag names B (its own folder), not A.
        assert f"folder:{folders['B'].id}" in by_key[docs["b"].document_key]["new_tags"]
