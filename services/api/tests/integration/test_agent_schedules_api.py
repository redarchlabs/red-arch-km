"""Integration tests for the agent-schedule routes (real PostgreSQL).

``agent_schedules`` and the sweep that fires them both existed, but nothing
exposed them — a standing instruction could only be created with direct database
access. These cover the route behaviour that matters for treating a schedule as
org configuration: it is created **off** by default, a malformed cron is refused
at write time, and changing the cron does not inherit the old firing time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentSchedule
from api.models.org import Org
from api.routers.agents import (
    _valid_cron,
    create_agent_schedule,
    delete_agent_schedule,
    list_agent_schedules,
    update_agent_schedule,
)
from api.schemas.agent import AgentScheduleCreate, AgentScheduleUpdate
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


class _Ctx:
    """Stands in for the OrgContext the route dependency injects."""

    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id


async def _seed(admin_session: AsyncSession) -> tuple[uuid.UUID, Agent]:
    org = Org(name=f"Sched-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    await set_tenant(admin_session, str(org.id))
    agent = Agent(name="briefer", org_id=org.id, provider="openai", model="gpt-5-mini", kind="operator")
    admin_session.add(agent)
    await admin_session.flush()
    return org.id, agent


class TestCreate:
    async def test_creates_disabled_by_default(self, admin_session: AsyncSession) -> None:
        """Configuring a roster must never start firing unattended work."""
        org_id, agent = await _seed(admin_session)

        created = await create_agent_schedule(
            AgentScheduleCreate(agent_id=agent.id, cron="0 9 * * *", task="Assemble the briefing"),
            _Ctx(org_id),
            admin_session,
        )

        assert created.enabled is False
        assert created.cron == "0 9 * * *"
        assert created.org_id == org_id

    async def test_enabled_can_be_opted_into(self, admin_session: AsyncSession) -> None:
        org_id, agent = await _seed(admin_session)

        created = await create_agent_schedule(
            AgentScheduleCreate(agent_id=agent.id, cron="0 9 * * *", task="x", enabled=True),
            _Ctx(org_id),
            admin_session,
        )

        assert created.enabled is True

    async def test_rejects_a_malformed_cron(self, admin_session: AsyncSession) -> None:
        """The sweep treats an unparseable cron as 'never due', so a typo would
        otherwise present as an agent that silently never runs."""
        org_id, agent = await _seed(admin_session)

        with pytest.raises(HTTPException) as exc:
            await create_agent_schedule(
                AgentScheduleCreate(agent_id=agent.id, cron="not a cron", task="x"),
                _Ctx(org_id),
                admin_session,
            )

        assert exc.value.status_code == 422
        assert (await admin_session.execute(select(AgentSchedule))).scalars().all() == []

    async def test_rejects_an_agent_from_another_org(self, admin_session: AsyncSession) -> None:
        org_id, _ = await _seed(admin_session)
        other_org_id, other_agent = await _seed(admin_session)

        with pytest.raises(HTTPException) as exc:
            await create_agent_schedule(
                AgentScheduleCreate(agent_id=other_agent.id, cron="0 9 * * *", task="x"),
                _Ctx(org_id),
                admin_session,
            )

        assert exc.value.status_code == 404


class TestUpdateAndList:
    async def test_changing_the_cron_clears_the_cached_next_firing(self, admin_session: AsyncSession) -> None:
        """next_run_at is derived; leaving a stale one would fire the new cron on
        the old schedule."""
        org_id, agent = await _seed(admin_session)
        created = await create_agent_schedule(
            AgentScheduleCreate(agent_id=agent.id, cron="0 9 * * *", task="x"),
            _Ctx(org_id),
            admin_session,
        )
        created.next_run_at = datetime(2026, 1, 1, tzinfo=UTC)
        await admin_session.flush()

        updated = await update_agent_schedule(
            created.id, AgentScheduleUpdate(cron="0 17 * * *"), _Ctx(org_id), admin_session
        )

        assert updated.cron == "0 17 * * *"
        assert updated.next_run_at is None

    async def test_updating_only_enabled_leaves_the_rest_alone(self, admin_session: AsyncSession) -> None:
        org_id, agent = await _seed(admin_session)
        created = await create_agent_schedule(
            AgentScheduleCreate(agent_id=agent.id, cron="0 9 * * *", task="Assemble the briefing"),
            _Ctx(org_id),
            admin_session,
        )

        updated = await update_agent_schedule(
            created.id, AgentScheduleUpdate(enabled=True), _Ctx(org_id), admin_session
        )

        assert updated.enabled is True
        assert updated.task == "Assemble the briefing"
        assert updated.cron == "0 9 * * *"

    async def test_update_rejects_a_malformed_cron(self, admin_session: AsyncSession) -> None:
        org_id, agent = await _seed(admin_session)
        created = await create_agent_schedule(
            AgentScheduleCreate(agent_id=agent.id, cron="0 9 * * *", task="x"),
            _Ctx(org_id),
            admin_session,
        )

        with pytest.raises(HTTPException) as exc:
            await update_agent_schedule(
                created.id, AgentScheduleUpdate(cron="every tuesday"), _Ctx(org_id), admin_session
            )

        assert exc.value.status_code == 422

    async def test_list_is_scoped_to_the_agent(self, admin_session: AsyncSession) -> None:
        org_id, agent = await _seed(admin_session)
        other = Agent(name="other", org_id=org_id, provider="openai", model="gpt-5-mini", kind="operator")
        admin_session.add(other)
        await admin_session.flush()
        for target in (agent, agent, other):
            await create_agent_schedule(
                AgentScheduleCreate(agent_id=target.id, cron="0 9 * * *", task="x"),
                _Ctx(org_id),
                admin_session,
            )

        listed = await list_agent_schedules(agent.id, _Ctx(org_id), admin_session)

        assert len(listed) == 2
        assert all(s.agent_id == agent.id for s in listed)

    async def test_another_orgs_schedule_is_not_reachable(self, admin_session: AsyncSession) -> None:
        org_id, agent = await _seed(admin_session)
        created = await create_agent_schedule(
            AgentScheduleCreate(agent_id=agent.id, cron="0 9 * * *", task="x"),
            _Ctx(org_id),
            admin_session,
        )
        other_org_id, _ = await _seed(admin_session)

        with pytest.raises(HTTPException) as exc:
            await update_agent_schedule(
                created.id, AgentScheduleUpdate(enabled=True), _Ctx(other_org_id), admin_session
            )

        assert exc.value.status_code == 404


class TestDelete:
    async def test_deletes_the_schedule(self, admin_session: AsyncSession) -> None:
        org_id, agent = await _seed(admin_session)
        created = await create_agent_schedule(
            AgentScheduleCreate(agent_id=agent.id, cron="0 9 * * *", task="x"),
            _Ctx(org_id),
            admin_session,
        )

        await delete_agent_schedule(created.id, _Ctx(org_id), admin_session)
        await admin_session.flush()

        assert await list_agent_schedules(agent.id, _Ctx(org_id), admin_session) == []

    async def test_deleting_an_unknown_schedule_is_a_404(self, admin_session: AsyncSession) -> None:
        org_id, _ = await _seed(admin_session)

        with pytest.raises(HTTPException) as exc:
            await delete_agent_schedule(uuid.uuid4(), _Ctx(org_id), admin_session)

        assert exc.value.status_code == 404


class TestCronValidator:
    @pytest.mark.parametrize("expr", ["0 9 * * *", "*/15 * * * *", "0 0 1 * *"])
    def test_accepts_real_expressions(self, expr: str) -> None:
        assert _valid_cron(expr) is True

    @pytest.mark.parametrize("expr", ["not a cron", "every tuesday", "99 * * * *", ""])
    def test_rejects_nonsense(self, expr: str) -> None:
        assert _valid_cron(expr) is False
