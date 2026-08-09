"""Work-order service — lifecycle + task/diary management with typed errors.

Enforces the status state machine (draft → awaiting_approval → approved →
in_progress → done | cancelled) so a caller can't jump an order to an invalid state.

Moving an *assigned* order to ``in_progress`` also **starts** it: a run is queued
for the assigned agent. Until this existed a work order was only a folder that
agent runs filed their diary into — assigning one recorded an intention nothing
acted on, so an order sat in ``in_progress`` looking exactly like work underway
while nothing was ever going to happen.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.work_order import WorkOrder, WorkOrderEntry, WorkOrderTask
from api.repositories.agent_run import AgentRunRepository
from api.repositories.work_order import WorkOrderRepository

# Allowed status transitions (terminal states have none).
_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"awaiting_approval", "approved", "in_progress", "cancelled"},
    "awaiting_approval": {"approved", "draft", "cancelled"},
    "approved": {"in_progress", "cancelled"},
    "in_progress": {"done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}

# The status that means work is happening — so it is the one that starts it.
# ``approved`` is deliberately not a start: approving is the human saying yes, and
# the two are separate acts in the state machine.
_DISPATCH_STATUS = "in_progress"

# A run in one of these states is already on the job; starting a second would put
# two agents on the same work — duplicated side effects and two billed LLM runs.
_LIVE_RUN_STATUSES = ("queued", "running", "waiting")


class WorkOrderError(Exception):
    pass


class WorkOrderNotFoundError(WorkOrderError):
    pass


class WorkOrderValidationError(WorkOrderError):
    pass


def _brief(wo: WorkOrder) -> str:
    """The task the agent wakes up to. The title alone is a label, not an
    instruction — the body is where the actual request lives."""
    body = (wo.body or "").strip()
    return f"{wo.title}\n\n{body}" if body else wo.title


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "wo"
    return f"{base}-{uuid.uuid4().hex[:6]}"


class WorkOrderService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = WorkOrderRepository(session, org_id)

    async def list_work_orders(self) -> list[WorkOrder]:
        return await self._repo.list_all()

    async def get_work_order(self, wo_id: uuid.UUID) -> WorkOrder:
        wo = await self._repo.get(wo_id)
        if wo is None:
            raise WorkOrderNotFoundError(f"Work order {wo_id} not found")
        return wo

    async def create_work_order(
        self,
        *,
        title: str,
        body: str | None = None,
        priority: str = "normal",
        assigned_agent_id: uuid.UUID | None = None,
        created_by_profile_id: uuid.UUID | None = None,
    ) -> WorkOrder:
        wo = WorkOrder(
            slug=_slugify(title),
            title=title,
            body=body,
            priority=priority,
            assigned_agent_id=assigned_agent_id,
            created_by_profile_id=created_by_profile_id,
            status="draft",
        )
        return await self._repo.create(wo)

    async def set_status(
        self,
        wo_id: uuid.UUID,
        new_status: str,
        *,
        actor_profile_id: uuid.UUID | None = None,
    ) -> WorkOrder:
        wo = await self.get_work_order(wo_id)
        if new_status == wo.status:
            return wo
        allowed = _TRANSITIONS.get(wo.status, set())
        if new_status not in allowed:
            raise WorkOrderValidationError(f"Cannot move work order from '{wo.status}' to '{new_status}'")
        if new_status == _DISPATCH_STATUS:
            # Before the status moves: a configuration that cannot run must not
            # leave the order claiming work is under way.
            await self._dispatch(wo, actor_profile_id=actor_profile_id)
        wo.status = new_status
        await self._repo.flush()
        return wo

    async def _dispatch(self, wo: WorkOrder, *, actor_profile_id: uuid.UUID | None) -> None:
        """Queue a run for the assigned agent, if there is one.

        Unassigned orders are left alone rather than refused: a work order is also
        a human tracking artifact, and requiring an agent to start one would break
        every order people work themselves.
        """
        if wo.assigned_agent_id is None:
            return
        agent = (
            await self._session.execute(
                select(Agent).where(Agent.id == wo.assigned_agent_id, Agent.org_id == self._org_id)
            )
        ).scalar_one_or_none()
        if agent is None or not agent.enabled:
            # Assigned-but-unrunnable is a broken configuration, not a human-only
            # order, so it is refused rather than silently skipped.
            raise WorkOrderValidationError(
                f"Work order '{wo.title}' is assigned to an agent that cannot run "
                "(missing or disabled). Reassign it or enable the agent."
            )
        if await self._has_live_run(wo.id):
            return

        run = await AgentRunRepository(self._session, self._org_id).create_run(
            agent_id=agent.id,
            provider=agent.provider,
            model=agent.model,
            trigger="work_order",
            input={"task": _brief(wo)},
            work_order_id=wo.id,
            # The agent works on behalf of whoever started the order, and reads the
            # knowledge base with exactly that person's entitlement. A run with no
            # actor is refused the knowledge base entirely (fail-closed), so it
            # would start and then be unable to read anything.
            actor_user_id=actor_profile_id,
            status="queued",
            label=f"Work order: {wo.title[:80]}",
        )
        await self._repo.add_entry(
            WorkOrderEntry(
                work_order_id=wo.id,
                agent_id=agent.id,
                agent_run_id=run.id,
                role=agent.name,
                text=f"Started: queued a run for {agent.name}.",
            )
        )

    async def _has_live_run(self, wo_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(AgentRun.id).where(
                AgentRun.work_order_id == wo_id,
                AgentRun.org_id == self._org_id,
                AgentRun.status.in_(_LIVE_RUN_STATUSES),
            )
        )
        return result.first() is not None

    async def assign(self, wo_id: uuid.UUID, agent_id: uuid.UUID | None) -> WorkOrder:
        wo = await self.get_work_order(wo_id)
        wo.assigned_agent_id = agent_id
        await self._repo.flush()
        return wo

    async def list_tasks(self, wo_id: uuid.UUID) -> list[WorkOrderTask]:
        await self.get_work_order(wo_id)
        return await self._repo.list_tasks(wo_id)

    async def set_tasks(self, wo_id: uuid.UUID, tasks: list[dict]) -> list[WorkOrderTask]:
        await self.get_work_order(wo_id)
        models = [
            WorkOrderTask(
                key=t.get("key") or f"T{i + 1}",
                title=t["title"],
                status=t.get("status", "pending"),
                sort_order=t.get("sort_order", i),
                assigned_agent_id=t.get("assigned_agent_id"),
            )
            for i, t in enumerate(tasks)
        ]
        return await self._repo.replace_tasks(wo_id, models)

    async def add_entry(
        self,
        wo_id: uuid.UUID,
        *,
        text: str,
        agent_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
        role: str | None = None,
    ) -> WorkOrderEntry:
        await self.get_work_order(wo_id)
        return await self._repo.add_entry(
            WorkOrderEntry(work_order_id=wo_id, text=text, agent_id=agent_id, agent_run_id=agent_run_id, role=role)
        )

    async def list_entries(self, wo_id: uuid.UUID) -> list[WorkOrderEntry]:
        await self.get_work_order(wo_id)
        return await self._repo.list_entries(wo_id)

    def progress(self, tasks: list[WorkOrderTask]) -> float:
        """Percent complete = done / (total excluding carried)."""
        counted = [t for t in tasks if t.status != "carried"]
        if not counted:
            return 0.0
        done = sum(1 for t in counted if t.status == "done")
        return round(done / len(counted), 3)
