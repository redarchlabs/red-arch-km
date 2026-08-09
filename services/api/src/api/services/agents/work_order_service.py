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

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentQuestion, AgentRun
from api.models.work_order import WorkOrder, WorkOrderEntry, WorkOrderTask
from api.repositories.agent_run import AgentRunRepository
from api.repositories.work_order import WorkOrderRepository
from api.schemas.work_order import MapEvent, MapLane, WorkOrderMap

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

# The lane a person occupies. Not an agent id, so it cannot collide with one.
_HUMAN_LANE = "human"

# How a run came to exist, as the label on its opening event. A consult and a
# delegation both create a child run, but only a consult blocks the parent.
_START_TITLES = {
    "work_order": "started the order",
    "consult": "consulted",
    "delegation": "took a delegated task",
    "schedule": "started on schedule",
    "manual": "started",
}

# Worst-first: a lane that is blocked reads as blocked even if it also finished
# something earlier, because the blocked run is the one that still needs a person.
_STATUS_RANK = ["error", "waiting", "running", "queued", "done", "cancelled"]


@dataclass(frozen=True)
class EntryPage:
    """ORM-side page: the route maps it to ``EntryPageRead``."""

    entries: list[WorkOrderEntry]
    has_more: bool


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


def _lane_for(agent: Agent) -> MapLane:
    return MapLane(key=str(agent.id), label=agent.name, avatar=agent.avatar, agent_kind=agent.kind)


def _roll_up(lane_key: str, runs: Sequence[Any]) -> str | None:
    """One status for a lane that may have run more than once."""
    statuses = [run.status for run, agent in runs if str(agent.id) == lane_key]
    for candidate in _STATUS_RANK:
        if candidate in statuses:
            return candidate
    return statuses[0] if statuses else None


def _run_events(runs: Sequence[Any]) -> list[MapEvent]:
    """Each run opens its lane and, once settled, closes it.

    A run still in flight deliberately gets no closing event: the lane ending at
    its last event is what makes "still going" visible without a spinner.
    """
    events: list[MapEvent] = []
    for run, agent in runs:
        events.append(
            MapEvent(
                id=f"s{run.id}",
                lane=str(agent.id),
                kind="delegated" if run.trigger == "delegation" else "started",
                at=run.created_at,
                title=_START_TITLES.get(run.trigger, run.trigger),
                detail=(run.input or {}).get("task"),
                run_id=run.id,
            )
        )
        if run.status in ("done", "error", "cancelled") and run.finished_at is not None:
            events.append(
                MapEvent(
                    id=f"f{run.id}",
                    lane=str(agent.id),
                    kind="failed" if run.status == "error" else "finished",
                    at=run.finished_at,
                    title=run.status,
                    detail=run.error,
                    run_id=run.id,
                )
            )
    return events


def _slugify(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "wo"
    return f"{base}-{uuid.uuid4().hex[:6]}"


class WorkOrderService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = WorkOrderRepository(session, org_id)

    @staticmethod
    def allowed_transitions(status: str) -> list[str]:
        """The statuses an order in this state may move to.

        Public so the API can send it: a client that re-derived this would offer
        buttons the server then rejects the moment the state machine changes.
        """
        return sorted(_TRANSITIONS.get(status, set()))

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

    async def list_entries_page(
        self, wo_id: uuid.UUID, *, limit: int = 20, before: uuid.UUID | None = None
    ) -> EntryPage:
        """A page of diary, newest first, returned oldest-first.

        The diary reads like a conversation: the newest entry is the one you want
        and history is something you scroll back into. Selecting newest-first and
        returning in reading order means the caller renders a slice top-to-bottom
        without reversing it, and lands with the newest at the bottom.
        """
        await self.get_work_order(wo_id)
        cursor: tuple[datetime, uuid.UUID] | None = None
        if before is not None:
            anchor = await self._repo.get_entry(wo_id, before)
            if anchor is None:
                # Falling back to the newest page would look to the reader like
                # the history jumped back to the present.
                raise WorkOrderNotFoundError(f"Diary entry {before} not found")
            cursor = (anchor.created_at, anchor.id)
        # One extra row answers "is there more" without a second COUNT.
        rows = await self._repo.entries_before(wo_id, limit=limit + 1, before=cursor)
        has_more = len(rows) > limit
        page = rows[:limit]
        page.reverse()
        return EntryPage(entries=page, has_more=has_more)

    async def interaction_map(self, wo_id: uuid.UUID) -> WorkOrderMap:
        """One lane per participant, with what each did placed in time.

        A tree of runs answers "who invoked whom", which is the least interesting
        question once more than two agents are involved — it says nothing about
        *when*, and nothing about who is idle versus blocked. Lanes over a shared
        clock show the shape of the work: parallel branches sit side by side,
        a gap is visibly a gap, and a lane that stops while another continues is
        obvious rather than inferred.

        Every event is derived from a structured row — runs, questions, approvals
        — never from parsing the diary's prose. Prose is written for people and
        changes freely; a map built on it silently loses events when the wording
        moves.
        """
        await self.get_work_order(wo_id)
        runs = (
            await self._session.execute(
                select(AgentRun, Agent)
                .join(Agent, Agent.id == AgentRun.agent_id)
                .where(AgentRun.work_order_id == wo_id, AgentRun.org_id == self._org_id)
                .order_by(AgentRun.created_at)
            )
        ).all()
        if not runs:
            return WorkOrderMap(lanes=[], events=[])

        lanes = {str(agent.id): _lane_for(agent) for _, agent in runs}
        events = _run_events(runs)
        events += await self._question_events(wo_id, [run.id for run, _ in runs])
        events += await self._approval_events([run.id for run, _ in runs], {run.id: agent for run, agent in runs})

        # The human lane only appears once someone is actually owed something —
        # an empty "you" track on every map would be noise.
        if any(e.lane == _HUMAN_LANE or e.target_lane == _HUMAN_LANE for e in events):
            lanes[_HUMAN_LANE] = MapLane(key=_HUMAN_LANE, label="You", avatar="🧑", agent_kind="human")
        for lane in lanes.values():
            lane.status = _roll_up(lane.key, runs)
        events.sort(key=lambda e: e.at)
        # Lanes in first-appearance order so the dispatched agent leads and each
        # peer appears under whoever pulled it in.
        order = {e.lane: i for i, e in enumerate(reversed(events))}
        return WorkOrderMap(
            lanes=sorted(lanes.values(), key=lambda ln: -order.get(ln.key, -1)),
            events=events,
        )

    async def _question_events(self, wo_id: uuid.UUID, run_ids: list[uuid.UUID]) -> list[MapEvent]:
        """Consults and questions: an arrow out of the asker, and the reply back.

        Two rows per question rather than one, because the gap between them *is*
        the block — collapsing them would hide exactly the interval you want to
        see.
        """
        if not run_ids:
            return []
        rows = (
            (
                await self._session.execute(
                    select(AgentQuestion).where(
                        AgentQuestion.run_id.in_(run_ids),
                        AgentQuestion.org_id == self._org_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        events: list[MapEvent] = []
        for q in rows:
            asker = str(q.asked_by_agent_id) if q.asked_by_agent_id else _HUMAN_LANE
            target = str(q.target_agent_id) if q.target_agent_id else _HUMAN_LANE
            events.append(
                MapEvent(
                    id=f"q{q.id}",
                    lane=asker,
                    kind="consulted" if q.audience == "agent" else "blocked",
                    at=q.created_at,
                    title="asked a peer" if q.audience == "agent" else "asked you",
                    detail=q.question,
                    target_lane=target,
                    run_id=q.run_id,
                )
            )
            if q.status == "answered" and q.answered_at is not None:
                events.append(
                    MapEvent(
                        id=f"a{q.id}",
                        lane=target,
                        kind="answered",
                        at=q.answered_at,
                        title="answered",
                        detail=q.answer,
                        target_lane=asker,
                    )
                )
        return events

    async def _approval_events(self, run_ids: list[uuid.UUID], agent_by_run: dict[uuid.UUID, Agent]) -> list[MapEvent]:
        """A pending approval is the one state a person can clear, so it is drawn
        as an arrow into the human lane rather than as another agent status."""
        if not run_ids:
            return []
        rows = (
            (
                await self._session.execute(
                    select(AgentApproval).where(
                        AgentApproval.run_id.in_(run_ids),
                        AgentApproval.org_id == self._org_id,
                        AgentApproval.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
        events: list[MapEvent] = []
        for a in rows:
            if a.run_id not in agent_by_run:
                continue
            detail = json.dumps(a.arguments) if a.arguments else None
            events.append(
                MapEvent(
                    id=f"ap{a.id}",
                    lane=str(agent_by_run[a.run_id].id),
                    kind="blocked",
                    at=a.created_at,
                    title=f"needs approval: {a.tool_name}",
                    detail=detail,
                    target_lane=_HUMAN_LANE,
                    run_id=a.run_id,
                    approval_id=a.id,
                )
            )
            # A matching card in the human lane, so the arrow joins two things
            # rather than trailing off into an empty row — and so the lane says
            # what is being asked of you instead of only that something is.
            events.append(
                MapEvent(
                    id=f"apw{a.id}",
                    lane=_HUMAN_LANE,
                    kind="blocked",
                    at=a.created_at,
                    title=f"approve {a.tool_name}",
                    detail=detail,
                    run_id=a.run_id,
                    approval_id=a.id,
                )
            )
        return events

    def progress(self, tasks: list[WorkOrderTask]) -> float:
        """Percent complete = done / (total excluding carried)."""
        counted = [t for t in tasks if t.status != "carried"]
        if not counted:
            return 0.0
        done = sum(1 for t in counted if t.status == "done")
        return round(done / len(counted), 3)
