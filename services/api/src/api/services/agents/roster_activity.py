"""What each agent is doing right now, for the roster page.

The Agents page listed name, kind, provider and model — everything that is true of an
agent when nothing is happening, and nothing that is true when something is. A person
looking at it during a live run saw exactly what they saw at 3am, so the only way to
learn that an agent was mid-task, or that it had been sitting on a question for an hour,
was to open the work order it happened to be attached to.

Two states are worth a badge, and they are not the same urgency:

* **working** — a run of theirs is queued or running. Informational; nothing to do.
* **needs you** — a person is the blocker: an approval waiting for a yes/no, or a
  question waiting for prose. This one is a call to action, so it wins when an agent has
  both (a second run can be underway while the first sits parked).

A run in ``waiting`` on a *peer consult* is deliberately not "needs you" — nobody is
being asked for anything, and a badge that cries for help when no help is wanted is a
badge people stop reading.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentApproval, AgentQuestion, AgentRun

# Run statuses that mean the agent is mid-task. ``waiting`` is excluded: a parked run
# is not working, it is blocked, and what it is blocked *on* decides the badge.
LIVE_STATUSES = ("queued", "running")


@dataclass(frozen=True)
class AgentActivity:
    agent_id: uuid.UUID
    state: str  # "working" | "needs_you"
    live_runs: int
    waiting_on_you: int


async def roster_activity(session: AsyncSession, org_id: uuid.UUID) -> list[AgentActivity]:
    """One row per agent that is doing something. Agents with nothing going on are
    absent rather than present-and-idle — the caller renders no badge for them."""
    live: dict[uuid.UUID, int] = {
        row.agent_id: row.n
        for row in (
            await session.execute(
                select(AgentRun.agent_id, func.count().label("n"))
                .where(
                    AgentRun.org_id == org_id,
                    AgentRun.status.in_(LIVE_STATUSES),
                    AgentRun.agent_id.is_not(None),
                )
                .group_by(AgentRun.agent_id)
            )
        ).all()
    }

    waiting: dict[uuid.UUID, int] = {}
    approvals = (
        await session.execute(
            select(AgentRun.agent_id, func.count().label("n"))
            .join(AgentApproval, AgentApproval.run_id == AgentRun.id)
            .where(
                AgentApproval.org_id == org_id,
                AgentApproval.status == "pending",
                AgentRun.agent_id.is_not(None),
            )
            .group_by(AgentRun.agent_id)
        )
    ).all()
    # Human questions only. A consult is an agent asking an agent — it blocks the
    # asker, but there is nothing here for a person to do.
    asked = (
        await session.execute(
            select(AgentRun.agent_id, func.count().label("n"))
            .join(AgentQuestion, AgentQuestion.run_id == AgentRun.id)
            .where(
                AgentQuestion.org_id == org_id,
                AgentQuestion.status == "pending",
                AgentQuestion.audience == "human",
                AgentRun.agent_id.is_not(None),
            )
            .group_by(AgentRun.agent_id)
        )
    ).all()
    for row in (*approvals, *asked):
        waiting[row.agent_id] = waiting.get(row.agent_id, 0) + row.n

    out = [
        AgentActivity(
            agent_id=agent_id,
            state="needs_you" if waiting.get(agent_id) else "working",
            live_runs=live.get(agent_id, 0),
            waiting_on_you=waiting.get(agent_id, 0),
        )
        for agent_id in {*live, *waiting}
    ]
    return sorted(out, key=lambda a: (a.state != "needs_you", str(a.agent_id)))
