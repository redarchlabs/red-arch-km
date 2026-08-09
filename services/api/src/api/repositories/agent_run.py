"""Repository for agent runs + transcript steps — org-scoped like the siblings.

Runs and steps are the durable record of an agent execution (the console tails
steps; the worker resumes parked runs). All queries filter by ``org_id``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentApproval, AgentRun, AgentRunStep

# States a run can still be moved out of; terminal states are immutable.
NONTERMINAL_STATUSES = ("queued", "running", "waiting")


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
    ) -> bool:
        """Compare-and-set terminal transition.

        Only a run still in a non-terminal state can be finalized; a run that was
        cancelled/timed out by another actor stays as that actor left it. Returns
        whether THIS call won the transition — on ``False`` the caller must take no
        further side effects on the run's behalf (no wire-back, no parent signal).

        Token counts **accumulate**. A resumed run starts the loop's counters at
        zero, so setting them here would discard everything the run spent before it
        parked — and a run that asks two questions would report only its last
        segment. Every question makes the under-count worse, so this is written as
        an increment against the stored value.
        """
        # Flush pending ORM changes (e.g. cleared resume state) before the raw
        # UPDATE so they aren't flushed later on top of the terminal row.
        await self._session.flush()
        result = await self._session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run.id,
                AgentRun.org_id == self._org_id,
                AgentRun.status.in_(NONTERMINAL_STATUSES),
            )
            .values(
                status=status,
                error=error,
                prompt_tokens=AgentRun.prompt_tokens + prompt_tokens,
                completion_tokens=AgentRun.completion_tokens + completion_tokens,
                total_tokens=AgentRun.total_tokens + total_tokens,
                wait_kind=None,
                finished_at=_now(),
                last_activity_at=_now(),
            )
        )
        won = int(getattr(result, "rowcount", 0) or 0) > 0
        await self._session.refresh(run)
        return won

    async def cancel_run(self, run_id: uuid.UUID, *, reason: str) -> bool:
        """Conditionally cancel a non-terminal run and void its pending approvals.

        Safe against the executor: its own terminal write is the same conditional
        UPDATE, so exactly one side wins; the cooperative check in the loop stops a
        mid-flight run within one turn.
        """
        result = await self._session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.org_id == self._org_id,
                AgentRun.status.in_(NONTERMINAL_STATUSES),
            )
            .values(status="cancelled", error=reason, wait_kind=None, finished_at=_now(), last_activity_at=_now())
        )
        won = int(getattr(result, "rowcount", 0) or 0) > 0
        if won:
            await self._session.execute(
                update(AgentApproval)
                .where(
                    AgentApproval.run_id == run_id,
                    AgentApproval.org_id == self._org_id,
                    AgentApproval.status == "pending",
                )
                .values(status="voided", decided_at=_now())
            )
        return won

    async def claim_run(self, run_id: uuid.UUID) -> AgentRun | None:
        """Compare-and-set ``queued`` → ``running``. Returns the run iff THIS caller won.

        The one rule for starting execution: whoever drives a run must first win
        this transition. Two parties can want the same queued run — the sweep in
        ``AgentRunExecutor._claim`` and a console waiting to resume inline — and if
        both proceed they replay the same pending tool batch. That failure is
        silent rather than loud: ``agent_run_steps.seq`` is ``max+1`` with no unique
        constraint, and there is no ``version_id_col``, so the transcripts
        interleave and the last write wins instead of raising. The visible damage
        is duplicated side effects and two billed LLM turns.

        Correct against the sweep by construction. The sweep selects
        ``FOR UPDATE SKIP LOCKED`` and updates in the same transaction, so this
        UPDATE either blocks on its row lock and then matches zero rows (READ
        COMMITTED re-evaluates the predicate after the lock releases), or gets
        there first and makes the row invisible to the sweep's ``status='queued'``
        filter.
        """
        result = await self._session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.org_id == self._org_id,
                AgentRun.status == "queued",
            )
            .values(status="running", last_activity_at=_now())
        )
        if int(getattr(result, "rowcount", 0) or 0) < 1:
            return None
        return await self.get_run(run_id)

    async def current_status(self, run_id: uuid.UUID) -> str | None:
        """The run's committed status, bypassing the ORM identity map — the
        cooperative-cancellation read (READ COMMITTED sees external commits)."""
        result = await self._session.execute(
            select(AgentRun.status).where(AgentRun.id == run_id, AgentRun.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def heartbeat(self, run_id: uuid.UUID) -> None:
        """Bump the lease heartbeat without touching ORM state."""
        await self._session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.org_id == self._org_id, AgentRun.status == "running")
            .values(last_activity_at=_now())
        )

    async def mark_waiting(
        self,
        run: AgentRun,
        wait_kind: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> AgentRun:
        """Park the run, banking what it spent up to this point.

        The resumed drive counts from zero, so usage not banked here is simply
        lost — a run that asks three questions would otherwise report only the work
        it did after the last answer.
        """
        run.status = "waiting"
        run.wait_kind = wait_kind
        run.prompt_tokens = (run.prompt_tokens or 0) + prompt_tokens
        run.completion_tokens = (run.completion_tokens or 0) + completion_tokens
        run.total_tokens = (run.total_tokens or 0) + total_tokens
        run.last_activity_at = _now()
        await self._session.flush()
        return run
