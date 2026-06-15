"""End-to-end tests for PostgreSQL Row-Level Security tenant isolation.

These tests prove that RLS policies prevent cross-tenant data leakage
even when application code would otherwise attempt to query across orgs.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.document import Document, Folder
from api.models.org import Org
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


class TestRLSIsolation:
    async def test_tenant_a_cannot_see_tenant_b_documents(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """Setting tenant=A should hide documents belonging to tenant=B."""
        # Seed two orgs (admin session has no tenant set yet)
        await set_tenant(admin_session, None)
        org_a = Org(name="OrgA", permission_number=1)
        org_b = Org(name="OrgB", permission_number=2)
        admin_session.add_all([org_a, org_b])
        await admin_session.commit()

        # Insert documents as each tenant
        await set_tenant(admin_session, str(org_a.id))
        doc_a = Document(title="A-doc", org_id=org_a.id, document_key=str(uuid.uuid4()))
        admin_session.add(doc_a)
        await admin_session.commit()

        await set_tenant(admin_session, str(org_b.id))
        doc_b = Document(title="B-doc", org_id=org_b.id, document_key=str(uuid.uuid4()))
        admin_session.add(doc_b)
        await admin_session.commit()

        # From a fresh session set to tenant A, we should only see A's doc
        await set_tenant(session, str(org_a.id))
        result = await session.execute(select(Document))
        visible = result.scalars().all()
        assert {d.title for d in visible} == {"A-doc"}

        # Switch to tenant B, only B's doc visible
        await set_tenant(session, str(org_b.id))
        result = await session.execute(select(Document))
        visible = result.scalars().all()
        assert {d.title for d in visible} == {"B-doc"}

    async def test_insert_wrong_tenant_rejected(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        """Inserting a document with mismatched org_id must fail the RLS INSERT policy."""
        await set_tenant(admin_session, None)
        org_a = Org(name="OrgInsertA", permission_number=10)
        org_b = Org(name="OrgInsertB", permission_number=11)
        admin_session.add_all([org_a, org_b])
        await admin_session.commit()

        # Tenant context = A, but attempt to insert with org_id = B
        await set_tenant(session, str(org_a.id))
        bogus = Document(title="cross-tenant", org_id=org_b.id, document_key=str(uuid.uuid4()))
        session.add(bogus)

        with pytest.raises(Exception):  # RLS policy violation raises
            await session.flush()

    async def test_no_tenant_set_returns_empty(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        """With no tenant context, queries against RLS-enabled tables return nothing."""
        await set_tenant(admin_session, None)
        org = Org(name="OrgNoCtx", permission_number=20)
        admin_session.add(org)
        await admin_session.commit()

        await set_tenant(admin_session, str(org.id))
        admin_session.add(Document(title="ghost", org_id=org.id, document_key=str(uuid.uuid4())))
        await admin_session.commit()

        # Fresh session with no tenant set
        result = await session.execute(select(Document))
        assert result.scalars().all() == []

    async def test_folder_rls_same_behavior(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        """Folders table should exhibit the same tenant isolation as documents."""
        await set_tenant(admin_session, None)
        org_a = Org(name="OrgFolderA", permission_number=30)
        org_b = Org(name="OrgFolderB", permission_number=31)
        admin_session.add_all([org_a, org_b])
        await admin_session.commit()

        await set_tenant(admin_session, str(org_a.id))
        admin_session.add(Folder(name="fA", org_id=org_a.id, dot_path="fA"))
        await admin_session.commit()

        await set_tenant(admin_session, str(org_b.id))
        admin_session.add(Folder(name="fB", org_id=org_b.id, dot_path="fB"))
        await admin_session.commit()

        await set_tenant(session, str(org_a.id))
        result = await session.execute(select(Folder))
        assert {f.name for f in result.scalars().all()} == {"fA"}
