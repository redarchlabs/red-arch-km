"""End-to-end tests for PostgreSQL Row-Level Security tenant isolation.

These tests prove that RLS policies prevent cross-tenant data leakage
even when application code would otherwise attempt to query across orgs.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.document import Document, Folder
from api.models.org import Org
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
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

        with pytest.raises(SQLAlchemyError):  # RLS policy violation raises a DB error
            await session.flush()

    async def test_no_tenant_set_returns_empty(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        """With no tenant context (GUC unset), queries against RLS tables return nothing.

        RED-3 hardening: after the ``nullif(..., '')`` fix the policy fails closed —
        an unset tenant yields an empty result rather than raising.
        """
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

    async def test_empty_string_tenant_guc_returns_empty(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """RED-3 regression: an *empty-string* tenant GUC must fail closed, not error.

        This reproduces the exact production hazard: on a pooled connection whose
        ``app.current_tenant_id`` was set then reverted, ``current_setting(name, true)``
        returns ``''`` (empty string), not NULL. The pre-hardening policy cast
        ``''::uuid`` and raised ``invalid input syntax for type uuid`` on the next
        RLS query. The hardened ``nullif(..., '')`` policy must instead return an
        empty result set (SELECT) and reject writes — with NO error raised.
        """
        await set_tenant(admin_session, None)
        org = Org(name="OrgEmptyGUC", permission_number=21)
        admin_session.add(org)
        await admin_session.commit()

        await set_tenant(admin_session, str(org.id))
        admin_session.add(Document(title="empty-guc-ghost", org_id=org.id, document_key=str(uuid.uuid4())))
        await admin_session.commit()

        # Simulate a reverted/pooled connection: GUC defined but empty, NOT unset.
        await session.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))

        # SELECT must return empty (fail closed) rather than raising a cast error.
        result = await session.execute(select(Document))
        assert result.scalars().all() == []

        # INSERT must be blocked by the WITH CHECK policy, not a cast error.
        session.add(Document(title="empty-guc-insert", org_id=org.id, document_key=str(uuid.uuid4())))
        with pytest.raises(SQLAlchemyError):
            await session.flush()

    async def test_privileged_role_sees_all_tenants(self, admin_session: AsyncSession, session: AsyncSession) -> None:
        """A privileged (BYPASSRLS/superuser) role can still read across every tenant.

        The seeding ``admin_session`` runs as the testcontainers superuser, which
        bypasses RLS entirely (mirrors a privileged maintenance/migration role in
        production). It must retain full cross-tenant visibility even though the
        enforcement ``session`` is scoped to a single tenant. This guards against
        an over-tightened policy that would starve legitimate privileged access.
        """
        await set_tenant(admin_session, None)
        org_a = Org(name="OrgPrivA", permission_number=40)
        org_b = Org(name="OrgPrivB", permission_number=41)
        admin_session.add_all([org_a, org_b])
        await admin_session.commit()

        await set_tenant(admin_session, str(org_a.id))
        admin_session.add(Document(title="priv-A", org_id=org_a.id, document_key=str(uuid.uuid4())))
        await admin_session.commit()
        await set_tenant(admin_session, str(org_b.id))
        admin_session.add(Document(title="priv-B", org_id=org_b.id, document_key=str(uuid.uuid4())))
        await admin_session.commit()

        # Enforcement session scoped to tenant A sees ONLY A's doc.
        await set_tenant(session, str(org_a.id))
        scoped = (await session.execute(select(Document))).scalars().all()
        assert {d.title for d in scoped} == {"priv-A"}

        # Privileged admin session (RLS-bypassing) sees BOTH tenants' docs.
        await set_tenant(admin_session, None)
        all_docs = (await admin_session.execute(select(Document))).scalars().all()
        titles = {d.title for d in all_docs}
        assert {"priv-A", "priv-B"} <= titles

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
