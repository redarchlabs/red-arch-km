"""Agent scheduler — fire cron-triggered agent runs.

The ``agent_schedules`` table (cron + task per agent) existed but nothing read it.
This sweep closes that gap: on the celery-beat cadence it finds every enabled
schedule that is *due* and enqueues a ``schedule``-triggered ``AgentRun`` for its
agent. The existing ``advance-runs`` sweep then claims and drives those queued
runs — so a daily "assemble the briefing" schedule turns into a real agent run.

Due-ness reuses the workflow engine's pure, unit-tested ``is_schedule_due`` so
agent and workflow cron share one implementation and one catch-up convention (a
never-fired schedule fires on the next sweep, then follows the cron boundaries).

Runs cross-org on the privileged session (like ``advance-runs``); every repo is
scoped by the schedule's ``org_id`` explicitly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.models.agent_run import AgentSchedule
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.services.workflow.schedule import is_schedule_due

logger = logging.getLogger(__name__)


def _next_cron(cron_expr: str, base: datetime) -> datetime | None:
    """Best-effort next cron boundary after ``base`` (for display in ``next_run_at``)."""
    try:
        from croniter import croniter

        if not croniter.is_valid(cron_expr):
            return None
        return croniter(cron_expr, base).get_next(datetime)
    except Exception:  # noqa: BLE001 - a bad expression just leaves next_run_at unset
        return None


async def run_due_schedules(
    session: AsyncSession, *, limit: int = 200, now: datetime | None = None
) -> dict[str, int]:
    """Enqueue a run for every enabled agent schedule that is due.

    Returns ``{"considered": n, "fired": m}``. The caller commits.
    """
    moment = now or datetime.now(UTC)
    # Cross-org scan of enabled schedules → needs the RLS bypass; each fire below
    # is written scoped by the schedule's own org_id.
    await db_scope.enter_bypass(session)
    schedules = (
        await session.execute(
            select(AgentSchedule).where(AgentSchedule.enabled.is_(True)).limit(limit)
        )
    ).scalars().all()

    fired = 0
    for sched in schedules:
        if not is_schedule_due({"cron": sched.cron}, sched.last_run_at, moment):
            continue
        agent = await AgentRepository(session, sched.org_id).get(sched.agent_id)
        if agent is None or not agent.enabled:
            continue
        await AgentRunRepository(session, sched.org_id).create_run(
            agent_id=agent.id,
            provider=agent.provider,
            model=agent.model,
            trigger="schedule",
            input={"task": sched.task},
            status="queued",  # picked up by the advance-runs sweep
            label="scheduled",
        )
        sched.last_run_at = moment
        sched.next_run_at = _next_cron(sched.cron, moment)
        fired += 1
        logger.info("agent schedule %s fired for agent %s (org %s)", sched.id, sched.agent_id, sched.org_id)

    return {"considered": len(schedules), "fired": fired}


async def upcoming_for_agent(
    session: AsyncSession, org_id: uuid.UUID, agent_id: uuid.UUID
) -> list[AgentSchedule]:
    """List an agent's schedules (org-scoped)."""
    return list(
        (
            await session.execute(
                select(AgentSchedule).where(
                    AgentSchedule.org_id == org_id, AgentSchedule.agent_id == agent_id
                )
            )
        ).scalars().all()
    )
