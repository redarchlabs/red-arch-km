"""Integration tests for the agents domain: service CRUD + validation and RLS
org isolation against a real PostgreSQL.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.org import Org
from api.schemas.agent import AgentCreate, AgentUpdate
from api.services.agents.service import (
    AgentConflictError,
    AgentService,
    AgentValidationError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed_two_orgs(admin_session: AsyncSession) -> tuple[Org, Org]:
    org_a = Org(name=f"AgtA-{uuid.uuid4().hex[:8]}", permission_number=1)
    org_b = Org(name=f"AgtB-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add_all([org_a, org_b])
    await admin_session.commit()
    return org_a, org_b


def _create(name: str, **over) -> AgentCreate:
    base = dict(name=name, provider="openai", model="gpt-5-mini", kind="operator")
    base.update(over)
    return AgentCreate(**base)


class TestAgentServiceValidation:
    async def test_create_and_get(self, admin_session: AsyncSession) -> None:
        org_a, _ = await _seed_two_orgs(admin_session)
        svc = AgentService(admin_session, org_a.id)
        agent = await svc.create_agent(_create("triage", display_name="Triage Bot"))
        await admin_session.commit()
        got = await svc.get_agent(agent.id)
        assert got.name == "triage"
        assert got.kind == "operator"

    async def test_provider_model_mismatch_rejected(self, admin_session: AsyncSession) -> None:
        org_a, _ = await _seed_two_orgs(admin_session)
        svc = AgentService(admin_session, org_a.id)
        with pytest.raises(AgentValidationError):
            await svc.create_agent(_create("bad", provider="anthropic", model="gpt-5"))

    async def test_duplicate_name_conflict(self, admin_session: AsyncSession) -> None:
        org_a, _ = await _seed_two_orgs(admin_session)
        svc = AgentService(admin_session, org_a.id)
        await svc.create_agent(_create("dup"))
        await admin_session.commit()
        with pytest.raises(AgentConflictError):
            await svc.create_agent(_create("dup"))

    async def test_supervisor_cycle_prevented(self, admin_session: AsyncSession) -> None:
        org_a, _ = await _seed_two_orgs(admin_session)
        svc = AgentService(admin_session, org_a.id)
        boss = await svc.create_agent(_create("boss", kind="coordinator"))
        worker = await svc.create_agent(_create("worker", supervisor_id=boss.id))
        await admin_session.commit()
        # Pointing the boss at its own report would close a loop.
        with pytest.raises(AgentValidationError):
            await svc.update_agent(boss.id, AgentUpdate(supervisor_id=worker.id))

    async def test_self_supervision_rejected(self, admin_session: AsyncSession) -> None:
        org_a, _ = await _seed_two_orgs(admin_session)
        svc = AgentService(admin_session, org_a.id)
        a = await svc.create_agent(_create("solo"))
        await admin_session.commit()
        with pytest.raises(AgentValidationError):
            await svc.update_agent(a.id, AgentUpdate(supervisor_id=a.id))


class TestAgentRLS:
    async def test_rls_hides_other_orgs_agents(
        self, session: AsyncSession, admin_session: AsyncSession
    ) -> None:
        org_a, org_b = await _seed_two_orgs(admin_session)
        admin_session.add_all(
            [
                Agent(name="a1", provider="openai", model="gpt-5-mini", org_id=org_a.id),
                Agent(name="b1", provider="openai", model="gpt-5-mini", org_id=org_b.id),
            ]
        )
        await admin_session.commit()

        await set_tenant(session, str(org_a.id))
        visible = (await session.execute(select(Agent.org_id))).scalars().all()
        assert set(visible) == {org_a.id}

        await set_tenant(session, str(org_b.id))
        visible_b = (await session.execute(select(Agent.org_id))).scalars().all()
        assert set(visible_b) == {org_b.id}
