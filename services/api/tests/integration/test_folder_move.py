"""Integration tests for folder move — verifies dot_path rebuild works on a real DB."""

from __future__ import annotations

import pytest
from api.models.document import Folder
from api.models.org import Org
from api.services.folder_service import FolderCycleError, move_folder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _make_folder(
    session: AsyncSession,
    *,
    name: str,
    org_id,  # type: ignore[no-untyped-def]
    parent_id=None,  # type: ignore[no-untyped-def]
    dot_path: str = "",
) -> Folder:
    folder = Folder(name=name, org_id=org_id, parent_id=parent_id, dot_path=dot_path)
    session.add(folder)
    await session.flush()
    return folder


class TestFolderMove:
    async def test_move_rebuilds_dot_paths(self, admin_session: AsyncSession) -> None:
        """Moving a folder updates its dot_path and all descendants'."""
        await set_tenant(admin_session, None)
        org = Org(name="MoveTest1", permission_number=1)
        admin_session.add(org)
        await admin_session.commit()

        await set_tenant(admin_session, str(org.id))

        # Tree: root
        #   ├── a (dot_path="a")
        #   │     └── b (dot_path="a.b")
        #   │           └── c (dot_path="a.b.c")
        #   └── x (dot_path="x")
        a = await _make_folder(admin_session, name="a", org_id=org.id, dot_path="a")
        b = await _make_folder(admin_session, name="b", org_id=org.id, parent_id=a.id, dot_path="a.b")
        await _make_folder(admin_session, name="c", org_id=org.id, parent_id=b.id, dot_path="a.b.c")
        x = await _make_folder(admin_session, name="x", org_id=org.id, dot_path="x")
        await admin_session.commit()

        # Move a under x → expect "x.a", "x.a.b", "x.a.b.c"
        await move_folder(admin_session, org.id, a, x.id)
        await admin_session.commit()

        # The subtree rewrite runs as raw SQL (bypassing the ORM), so cached
        # descendant objects hold stale dot_paths — populate_existing forces the
        # query to overwrite them with fresh DB values. Scope to this org:
        # admin_session is a superuser that bypasses RLS, so an unscoped query
        # would also see folders committed by sibling tests.
        result = await admin_session.execute(
            select(Folder)
            .where(Folder.org_id == org.id)
            .order_by(Folder.name)
            .execution_options(populate_existing=True)
        )
        folders = {f.name: f.dot_path for f in result.scalars().all()}
        assert folders["a"] == "x.a"
        assert folders["b"] == "x.a.b"
        assert folders["c"] == "x.a.b.c"
        assert folders["x"] == "x"

    async def test_move_to_root(self, admin_session: AsyncSession) -> None:
        await set_tenant(admin_session, None)
        org = Org(name="MoveTest2", permission_number=2)
        admin_session.add(org)
        await admin_session.commit()

        await set_tenant(admin_session, str(org.id))
        a = await _make_folder(admin_session, name="a", org_id=org.id, dot_path="a")
        b = await _make_folder(admin_session, name="b", org_id=org.id, parent_id=a.id, dot_path="a.b")
        await admin_session.commit()

        # Move b to root → new dot_path = "b"
        await move_folder(admin_session, org.id, b, None)
        await admin_session.commit()

        refreshed = await admin_session.execute(
            select(Folder).where(Folder.name == "b", Folder.org_id == org.id).execution_options(populate_existing=True)
        )
        folder = refreshed.scalar_one()
        assert folder.dot_path == "b"
        assert folder.parent_id is None

    async def test_cycle_rejected(self, admin_session: AsyncSession) -> None:
        await set_tenant(admin_session, None)
        org = Org(name="MoveTest3", permission_number=3)
        admin_session.add(org)
        await admin_session.commit()

        await set_tenant(admin_session, str(org.id))
        a = await _make_folder(admin_session, name="a", org_id=org.id, dot_path="a")
        b = await _make_folder(admin_session, name="b", org_id=org.id, parent_id=a.id, dot_path="a.b")
        await admin_session.commit()

        # Attempt to move a under b — would create a cycle
        with pytest.raises(FolderCycleError):
            await move_folder(admin_session, org.id, a, b.id)

    async def test_prefix_similar_names_not_affected(self, admin_session: AsyncSession) -> None:
        """Moving 'alpha' should not rewrite 'alpha2' (prefix match bug guard)."""
        await set_tenant(admin_session, None)
        org = Org(name="MoveTest4", permission_number=4)
        admin_session.add(org)
        await admin_session.commit()

        await set_tenant(admin_session, str(org.id))
        alpha = await _make_folder(admin_session, name="alpha", org_id=org.id, dot_path="alpha")
        await _make_folder(admin_session, name="alpha2", org_id=org.id, dot_path="alpha2")
        target = await _make_folder(admin_session, name="target", org_id=org.id, dot_path="target")
        await admin_session.commit()

        await move_folder(admin_session, org.id, alpha, target.id)
        await admin_session.commit()

        result = await admin_session.execute(
            select(Folder)
            .where(Folder.org_id == org.id)
            .order_by(Folder.name)
            .execution_options(populate_existing=True)
        )
        folders = {f.name: f.dot_path for f in result.scalars().all()}
        assert folders["alpha"] == "target.alpha"
        assert folders["alpha2"] == "alpha2"  # untouched
