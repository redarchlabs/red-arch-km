"""Repository for open agent questions — org-scoped like its inbox siblings."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentQuestion


class AgentQuestionRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, question_id: uuid.UUID) -> AgentQuestion | None:
        result = await self._session.execute(
            select(AgentQuestion).where(AgentQuestion.id == question_id, AgentQuestion.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def list_pending(self, *, audience: str | None = None) -> list[AgentQuestion]:
        stmt = select(AgentQuestion).where(AgentQuestion.org_id == self._org_id, AgentQuestion.status == "pending")
        if audience is not None:
            stmt = stmt.where(AgentQuestion.audience == audience)
        return list((await self._session.execute(stmt.order_by(AgentQuestion.created_at))).scalars().all())

    async def pending_for_peer_run(self, peer_run_id: uuid.UUID) -> AgentQuestion | None:
        """The question a consult run was spawned to answer, if still open.

        Keyed off the run rather than the run's ``input`` payload so a run whose
        input was rewritten (resume state, lease requeue) still resolves.
        """
        result = await self._session.execute(
            select(AgentQuestion).where(
                AgentQuestion.peer_run_id == peer_run_id,
                AgentQuestion.org_id == self._org_id,
                AgentQuestion.status == "pending",
            )
        )
        return result.scalars().first()

    async def pending_for_asking_run(self, run_id: uuid.UUID) -> list[AgentQuestion]:
        result = await self._session.execute(
            select(AgentQuestion).where(
                AgentQuestion.run_id == run_id,
                AgentQuestion.org_id == self._org_id,
                AgentQuestion.status == "pending",
            )
        )
        return list(result.scalars().all())
