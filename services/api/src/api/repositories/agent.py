"""Repository for agents — org-scoped like the other tenant repos.

Every query filters by ``org_id`` explicitly for defence in depth on top of RLS,
matching the ApiKey/WorkflowConnection repos.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent


class AgentRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[Agent]:
        result = await self._session.execute(
            select(Agent).where(Agent.org_id == self._org_id).order_by(Agent.name)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Agent).where(Agent.org_id == self._org_id)
        )
        return int(result.scalar_one())

    async def get(self, agent_id: uuid.UUID) -> Agent | None:
        result = await self._session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Agent | None:
        result = await self._session.execute(
            select(Agent).where(Agent.name == name, Agent.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def list_direct_reports(self, supervisor_id: uuid.UUID) -> list[Agent]:
        result = await self._session.execute(
            select(Agent).where(
                Agent.org_id == self._org_id, Agent.supervisor_id == supervisor_id
            )
        )
        return list(result.scalars().all())

    async def create(self, agent: Agent) -> Agent:
        agent.org_id = self._org_id
        self._session.add(agent)
        await self._session.flush()
        return agent

    async def flush(self) -> None:
        await self._session.flush()

    async def delete(self, agent: Agent) -> None:
        await self._session.delete(agent)
        await self._session.flush()
