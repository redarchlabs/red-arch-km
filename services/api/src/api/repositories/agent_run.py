"""Repository for agent runs + transcript steps — org-scoped like the siblings.

Runs and steps are the durable record of an agent execution (the console tails
steps; the worker resumes parked runs). All queries filter by ``org_id``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentRun, AgentRunStep


def _now() -> datetime:
    return datetime.now(UTC)


class AgentRunRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def create_run(
        self,
        *,
        agent_id: uuid.UUID | None,
        provider: str | None,
        model: str | None,
        trigger: str = "manual",
        input: dict | None = None,
        actor_user_id: uuid.UUID | None = None,
        work_order_id: uuid.UUID | None = None,
        parent_run_id: uuid.UUID | None = None,
        label: str | None = None,
        status: str = "running",
    ) -> AgentRun:
        run = AgentRun(
            agent_id=agent_id,
            provider=provider,
            model=model,
            trigger=trigger,
            input=input or {},
            actor_user_id=actor_user_id,
            work_order_id=work_order_id,
            parent_run_id=parent_run_id,
            label=label,
            status=status,
            started_at=_now(),
            last_activity_at=_now(),
            org_id=self._org_id,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(self, run_id: uuid.UUID) -> AgentRun | None:
        result = await self._session.execute(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def list_runs(self, *, agent_id: uuid.UUID | None = None, limit: int = 50) -> list[AgentRun]:
        stmt = select(AgentRun).where(AgentRun.org_id == self._org_id)
        if agent_id is not None:
            stmt = stmt.where(AgentRun.agent_id == agent_id)
        stmt = stmt.order_by(AgentRun.created_at.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def next_seq(self, run_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(AgentRunStep.seq), -1)).where(AgentRunStep.run_id == run_id)
        )
        return int(result.scalar_one()) + 1

    async def add_step(
        self,
        run_id: uuid.UUID,
        *,
        kind: str,
        name: str | None = None,
        content: dict | None = None,
        tokens: int | None = None,
        seq: int | None = None,
    ) -> AgentRunStep:
        if seq is None:
            seq = await self.next_seq(run_id)
        step = AgentRunStep(
            run_id=run_id,
            seq=seq,
            kind=kind,
            name=name,
            content=content or {},
            tokens=tokens,
            org_id=self._org_id,
        )
        self._session.add(step)
        await self._session.flush()
        return step

    async def list_steps(self, run_id: uuid.UUID) -> list[AgentRunStep]:
        result = await self._session.execute(
            select(AgentRunStep)
            .where(AgentRunStep.run_id == run_id, AgentRunStep.org_id == self._org_id)
            .order_by(AgentRunStep.seq)
        )
        return list(result.scalars().all())

    async def finalize_run(
        self,
        run: AgentRun,
        *,
        status: str,
        error: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> AgentRun:
        run.status = status
        run.error = error
        run.prompt_tokens = prompt_tokens
        run.completion_tokens = completion_tokens
        run.total_tokens = total_tokens
        run.finished_at = _now()
        run.last_activity_at = _now()
        await self._session.flush()
        return run

    async def mark_waiting(self, run: AgentRun, wait_kind: str) -> AgentRun:
        run.status = "waiting"
        run.wait_kind = wait_kind
        run.last_activity_at = _now()
        await self._session.flush()
        return run
