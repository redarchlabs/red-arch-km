"""Tests for the GUC-gated RLS bypass (migration 034 / api.db_scope).

The app connects as a non-superuser role. Cross-org / no-tenant-context paths
opt into the permissive ``admin_bypass_all`` policy by setting ``app.bypass='on'``
(``db_scope.enter_bypass``); everything else stays fully RLS-isolated. These
tests prove both halves: the bypass widens read AND write across orgs, and RLS
still fails closed when it is off.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.document import Document
from api.models.org import Org
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_bypass, set_tenant

pytestmark = pytest.mark.integration


async def _seed_two_orgs(admin_session: AsyncSession, base: int) -> tuple[Org, Org]:
    """Seed one document in each of two orgs via the privileged admin session.

    ``base`` must be unique per test — committed org rows persist across tests and
    ``permission_number`` collisions would fail the seed.
    """
    await set_tenant(admin_session, None)
    org_a = Org(name=f"BypassA{base}", permission_number=base)
    org_b = Org(name=f"BypassB{base}", permission_number=base + 1)
    admin_session.add_all([org_a, org_b])
    await admin_session.commit()

    await set_tenant(admin_session, str(org_a.id))
    admin_session.add(Document(title=f"A{base}", org_id=org_a.id, document_key=str(uuid.uuid4())))
    await admin_session.commit()

    await set_tenant(admin_session, str(org_b.id))
    admin_session.add(Document(title=f"B{base}", org_id=org_b.id, document_key=str(uuid.uuid4())))
    await admin_session.commit()

    return org_a, org_b


class TestRLSBypass:
    async def test_cross_org_read_denied_without_bypass(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """No tenant + no bypass → RLS fails closed (zero rows), even cross-org."""
        await _seed_two_orgs(admin_session, 9010)
        await set_tenant(session, None)
        await set_bypass(session, False)
        rows = (await session.execute(select(Document))).scalars().all()
        assert rows == []

    async def test_cross_org_read_allowed_with_bypass(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """With app.bypass='on' the non-superuser session sees every org's rows."""
        await _seed_two_orgs(admin_session, 9020)
        await set_tenant(session, None)
        await set_bypass(session, True)
        titles = {d.title for d in (await session.execute(select(Document))).scalars().all()}
        assert {"A9020", "B9020"} <= titles

    async def test_cross_org_write_allowed_with_bypass(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """The bypass is read+write (WITH CHECK): a write to another org succeeds.

        This is the case KM2's workflow/agent sweeps need (cross-org batch claims)
        that a SELECT-only bypass could not express.
        """
        _, org_b = await _seed_two_orgs(admin_session, 9030)
        await set_tenant(session, None)
        await set_bypass(session, True)
        session.add(Document(title="into-B", org_id=org_b.id, document_key=str(uuid.uuid4())))
        await session.flush()  # must NOT raise
        titles = {d.title for d in (await session.execute(select(Document))).scalars().all()}
        assert "into-B" in titles

    async def test_write_to_other_org_denied_when_bypass_off(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """Scoped to org A (bypass off), inserting a row for org B is rejected."""
        org_a, org_b = await _seed_two_orgs(admin_session, 9040)
        await set_bypass(session, False)
        await set_tenant(session, str(org_a.id))
        session.add(Document(title="X-into-B", org_id=org_b.id, document_key=str(uuid.uuid4())))
        with pytest.raises(SQLAlchemyError):
            await session.flush()

    async def test_tenant_scope_holds_with_bypass_off(
        self, admin_session: AsyncSession, session: AsyncSession
    ) -> None:
        """Bypass off + tenant=A → only A's rows, exactly as before the refactor."""
        org_a, _ = await _seed_two_orgs(admin_session, 9050)
        await set_bypass(session, False)
        await set_tenant(session, str(org_a.id))
        titles = {d.title for d in (await session.execute(select(Document))).scalars().all()}
        assert titles == {"A9050"}
