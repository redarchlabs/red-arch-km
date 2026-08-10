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
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentNotification, AgentQuestion, AgentRun
from api.models.work_order import (
    WORK_ORDER_MODES,
    WORK_ORDER_TASK_STATUSES,
    WorkOrder,
    WorkOrderArtifact,
    WorkOrderEntry,
    WorkOrderTask,
)
from api.repositories.agent_run import AgentRunRepository
from api.repositories.work_order import WorkOrderRepository
from api.repositories.work_order_artifacts import WorkOrderArtifactRepository
from api.schemas.work_order import MapEvent, MapLane, WorkOrderMap
from api.services.agents.acceptance import check_acceptance
from api.services.agents.attachments import mime_for
from api.services.agents.capability import capability_warnings
from api.services.agents.notify import create_notification

logger = logging.getLogger(__name__)

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

# The lane the platform itself writes in — stalls, capability gaps, blocked steps.
# Matches ``run_executor._STALL_ROLE`` so all machine notices read as one voice.
_SYSTEM_LANE = "system"

# Diary prefixes. They are markers as well as prose: a notice already written is
# found by prefix, which is how each of these stays once-per-situation instead of
# once-per-poll. Changing one re-arms its notice for every open order.
_CAPABILITY_MARKER = "⚠️ Capability gap:"
_BLOCKED_MARKER = "⛔ Blocked:"
_DONE_MARKER = "✅ Done:"
_ACCEPTANCE_MARKER = "🎯 Did not answer the request:"
_CONTINUE_MARKER = "▶️ Continuing:"

# Shortest evidence that can say anything. Not a quality bar — a floor that "done",
# "ok" and "finished" fall below, which is most of what an unforced field receives.
_MIN_EVIDENCE_CHARS = 25

# The step appended to a plan that owes somebody something. Matched by prefix when
# deciding whether the plan already has one, so re-planning does not stack copies.
DELIVERY_TASK_TITLE = "Attach the deliverable to this work order (document, CSV, or file) and say what it contains"

# Words in a brief that promise an output a person will open. An order that only asks
# for an opinion — "what do you think of our pricing" — owes a reply, not a file, and
# adding a delivery step there is bureaucracy that teaches people to ignore the plan.
_DELIVERABLE_WORDS = re.compile(
    r"\b(report|audit|analysis|analyse|analyze|summary|summarise|summarize|write[ -]?up|deck|"
    r"spreadsheet|csv|export|document|doc|spec|design|plan|proposal|draft|inventory|list of|"
    r"screenshots?|deliverable)\b",
    re.I,
)

# Steps that already promise delivery. Checked against the plan's own titles so an
# agent that thought of it first is not given a duplicate.
_DELIVERY_WORDS = re.compile(r"\b(attach|upload|deliver|hand over|publish|submit)\b", re.I)


def wants_deliverable(text: str) -> bool:
    """Does this brief promise something a person will open at the end?"""
    return bool(_DELIVERABLE_WORDS.search(text))


def has_delivery_step(titles: Sequence[str]) -> bool:
    return any(_DELIVERY_WORDS.search(t) for t in titles)


# How a run came to exist, as the label on its opening event. A consult and a
# delegation both create a child run, but only a consult blocks the parent.
_START_TITLES = {
    "work_order": "started the order",
    "consult": "consulted",
    "delegation": "took a delegated task",
    "escalation": "picked up an escalation",
    "continuation": "picked the order back up",
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


# How much of the diary a follow-up run is given. Enough that the agent knows what
# has already been tried, bounded so a long-running order does not grow its own
# prompt without limit.
_REPLY_CONTEXT_ENTRIES = 12


def _reply_brief(wo: WorkOrder, history: Sequence[WorkOrderEntry], reply: str) -> str:
    """The brief for a run started by a person replying.

    A reply is almost always a *response* — an answer, a correction, a go-ahead —
    so a run given only the reply would restart the order from nothing and repeat
    the work already in the diary.
    """
    lines = [f"{e.role}: {e.text.strip()}" for e in history if e.text.strip()]
    transcript = "\n\n".join(lines[-_REPLY_CONTEXT_ENTRIES:])
    return (
        f"{_brief(wo)}\n\n"
        "--- What has happened on this work order so far ---\n"
        f"{transcript}\n\n"
        "--- The person who filed it has just replied ---\n"
        f"{reply}\n\n"
        "Continue the work order from here, taking their reply as the instruction."
    )


# How many times an order may be picked up again after its agents stop. High enough
# for a real multi-step job that closes a few steps per run, low enough that a chain
# which cannot finish stops costing money and asks a person instead.
MAX_CONTINUATIONS = 8


def _continuation_brief(wo: WorkOrder, tasks: Sequence[WorkOrderTask], history: Sequence[WorkOrderEntry]) -> str:
    """The brief for picking an order back up where its last agent left it.

    A run ends when the model stops calling tools, which is not the same as the work
    being finished — an agent routinely closes one step, sets the next to in_progress,
    writes a tidy summary and stops. Nothing then continued the order, so a nine-step
    job needed nine separate human nudges. This is the brief that replaces the nudge.

    It leads with the checklist, because the observed failure of a restarted run is
    re-planning from scratch: told only "carry on", a model rewrites the task list and
    the order loses the work already done.
    """
    done = [t for t in tasks if t.status in ("done", "carried")]
    outstanding = [t for t in tasks if t.status not in ("done", "carried")]
    lines = [f"{e.role}: {e.text.strip()}" for e in history if e.text.strip()]
    return (
        f"{_brief(wo)}\n\n"
        "--- This work order is already under way. You are continuing it, not starting it. ---\n"
        "ALREADY DONE (do not repeat, do not re-plan):\n"
        + ("\n".join(f"  {t.key} [{t.status}] {t.title}" for t in done) or "  (nothing yet)")
        + "\n\nSTILL OPEN — this is your work:\n"
        + "\n".join(f"  {t.key} [{t.status}] {t.title}" for t in outstanding)
        + "\n\n--- What has happened so far ---\n"
        + "\n\n".join(lines[-_REPLY_CONTEXT_ENTRIES:])
        + "\n\nKeep the existing task list — do not call set_work_order_tasks unless the plan is "
        "genuinely wrong. Work the open steps and mark each one as you finish it. Do not stop to "
        "report progress: a summary is not a step, and the order continues only while there is work "
        "you can still do. If you truly cannot proceed, block the specific step and say what would "
        "unblock it, or escalate."
    )


def _attachment_lines(artifacts: list[Any]) -> str:
    """How attachments read in the diary — a name a person recognises."""
    return "\n".join(f"📎 {a.filename or a.document_id}" for a in artifacts)


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
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, settings: Settings | None = None) -> None:
        self._session = session
        self._org_id = org_id
        self._repo = WorkOrderRepository(session, org_id)
        # Optional so the many callers that only read an order need not thread config
        # through. Where it is present, notifications can also leave the app (email,
        # the org's notify workflow) instead of only landing in the in-app inbox.
        self._settings = settings

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
        mode: str = "manual",
        review_level: str = "standard",
        assigned_agent_id: uuid.UUID | None = None,
        created_by_profile_id: uuid.UUID | None = None,
    ) -> WorkOrder:
        wo = WorkOrder(
            slug=_slugify(title),
            title=title,
            body=body,
            priority=priority,
            mode=mode,
            review_level=review_level,
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

    async def _dispatch(
        self,
        wo: WorkOrder,
        *,
        actor_profile_id: uuid.UUID | None,
        task: str | None = None,
        ignore_run_id: uuid.UUID | None = None,
        attachment_ids: list[uuid.UUID] | None = None,
    ) -> None:
        """Queue a run for the assigned agent, if there is one.

        Unassigned orders are left alone rather than refused: a work order is also
        a human tracking artifact, and requiring an agent to start one would break
        every order people work themselves.
        """
        if wo.assigned_agent_id is None:
            # Not refused, but not silent either. Starting an order that will not
            # run looks identical to starting one that will, and the only place
            # anyone would look for the reason is the order's own record.
            await self._repo.add_entry(
                WorkOrderEntry(
                    work_order_id=wo.id,
                    role=_HUMAN_LANE,
                    text=("Started with no agent assigned, so no run was queued. Assign an agent to have it worked."),
                )
            )
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
        if await self._has_live_run(wo.id, ignore_run_id=ignore_run_id):
            return

        run = await AgentRunRepository(self._session, self._org_id).create_run(
            agent_id=agent.id,
            provider=agent.provider,
            model=agent.model,
            trigger="work_order",
            # Attachment ids ride on the run's input, not the brief: the executor
            # loads the bytes at turn time and only for a model that can see them,
            # so nothing here carries an image.
            input={
                "task": task or _brief(wo),
                **({"attachments": [str(i) for i in attachment_ids]} if attachment_ids else {}),
            },
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
        await self._warn_on_capability_gap(wo, agent, run_id=run.id)

    async def _warn_on_capability_gap(self, wo: WorkOrder, agent: Agent, *, run_id: uuid.UUID | None) -> None:
        """Say at the start what the assignee's chain cannot do for this order.

        Checked here rather than at assignment because starting is the moment the
        order begins to look like work in progress, and this is the last point before
        anyone is waiting on it. Never raises: a wrong or failed heuristic must not
        stop an order a person deliberately started.
        """
        try:
            warnings = await capability_warnings(
                self._session,
                self._org_id,
                agent,
                _brief(wo),
                settings=self._settings,
                autonomy=await self._org_autonomy(wo),
            )
        except Exception:  # noqa: BLE001 - advisory check, never fatal to a dispatch
            logger.warning("capability check failed for work order %s", wo.id)
            return
        if not warnings or await self._already_said(wo.id, _CAPABILITY_MARKER):
            return
        text = f"{_CAPABILITY_MARKER} " + " ".join(warnings)
        await self._repo.add_entry(WorkOrderEntry(work_order_id=wo.id, role=_SYSTEM_LANE, text=text))
        await create_notification(
            self._session,
            self._org_id,
            kind="escalation",
            title=f"“{wo.title}” was started by an agent that may not be able to do it",
            body=" ".join(warnings),
            work_order_id=wo.id,
            run_id=run_id,
            recipient_role="org_admin",
            settings=self._settings,
        )

    async def _org_autonomy(self, wo: WorkOrder) -> str:
        from api.models.org import Org
        from api.services.agents.authority import posture_for

        org = (await self._session.execute(select(Org).where(Org.id == self._org_id))).scalar_one_or_none()
        return posture_for(wo, getattr(org, "agent_autonomy", None) or "high_touch")

    async def _already_said(self, wo_id: uuid.UUID, marker: str) -> bool:
        """Has a notice with this prefix already been filed on this order?

        The diary is the throttle: it survives restarts, it is per-order, and it is
        the same place a person reads the notice — so "already told them" and "they
        can see it" can never drift apart.
        """
        query = select(WorkOrderEntry.id).where(
            WorkOrderEntry.work_order_id == wo_id,
            WorkOrderEntry.org_id == self._org_id,
            WorkOrderEntry.text.like(f"{marker}%"),
        )
        return (await self._session.execute(query)).first() is not None

    async def continue_order(self, wo: WorkOrder, tasks: Sequence[WorkOrderTask]) -> AgentRun | None:
        """Pick a stalled order back up, or return None with the reason left in place.

        Only the sweeper calls this, and only for an order that is in progress, has
        open steps and has nobody working it. The judgement about *whether* to
        continue lives in the caller; this owns the how.
        """
        if wo.assigned_agent_id is None:
            return None
        agent = (
            await self._session.execute(
                select(Agent).where(Agent.id == wo.assigned_agent_id, Agent.org_id == self._org_id)
            )
        ).scalar_one_or_none()
        if agent is None or not agent.enabled:
            return None
        history = (await self.list_entries_page(wo.id, limit=_REPLY_CONTEXT_ENTRIES)).entries
        settled = sum(1 for t in tasks if t.status in ("done", "carried"))
        run = await AgentRunRepository(self._session, self._org_id).create_run(
            agent_id=agent.id,
            provider=agent.provider,
            model=agent.model,
            trigger="continuation",
            input={
                "task": _continuation_brief(wo, tasks, history),
                # Carried so the next sweep can tell a continuation that moved the
                # work from one that only restated it. A loop that makes no progress
                # is a loop that will not make any, and it bills for every turn.
                "_continuation_progress": settled,
            },
            work_order_id=wo.id,
            actor_user_id=wo.created_by_profile_id,
            status="queued",
            label=f"Continuing: {wo.title[:70]}",
        )
        await self._repo.add_entry(
            WorkOrderEntry(
                work_order_id=wo.id,
                agent_id=agent.id,
                agent_run_id=run.id,
                role=_SYSTEM_LANE,
                text=f"{_CONTINUE_MARKER} picked the order back up ({settled} of {len(tasks)} steps settled).",
            )
        )
        return run

    async def has_live_run(self, wo_id: uuid.UUID) -> bool:
        """Is an agent on this order right now?

        Public because a caller that has just replied needs to tell a person whether
        that started anything — "recorded" and "restarted" are different outcomes and
        guessing between them is how a message looks delivered when it was not.
        """
        return await self._has_live_run(wo_id)

    async def _has_live_run(self, wo_id: uuid.UUID, *, ignore_run_id: uuid.UUID | None = None) -> bool:
        """Is another run already on this order?

        ``ignore_run_id`` excludes the caller's own run: a tool that queues the
        next run from inside a run that is about to finish would otherwise see
        itself and skip.
        """
        query = select(AgentRun.id).where(
            AgentRun.work_order_id == wo_id,
            AgentRun.org_id == self._org_id,
            AgentRun.status.in_(_LIVE_RUN_STATUSES),
        )
        if ignore_run_id is not None:
            query = query.where(AgentRun.id != ignore_run_id)
        return (await self._session.execute(query)).first() is not None

    async def assign(
        self,
        wo_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        *,
        actor_profile_id: uuid.UUID | None = None,
    ) -> WorkOrder:
        wo = await self.get_work_order(wo_id)
        wo.assigned_agent_id = agent_id
        await self._repo.flush()
        # Dispatch is normally the status edge into in_progress. An order started
        # before anyone was assigned passed that edge with nothing to dispatch, so
        # assigning is the moment it becomes runnable — without this it sits
        # in_progress, with an agent, forever, and nothing says why.
        if agent_id is not None and wo.status == _DISPATCH_STATUS:
            await self._dispatch(wo, actor_profile_id=actor_profile_id)
            await self._repo.flush()
        return wo

    async def set_mode(self, wo_id: uuid.UUID, mode: str) -> WorkOrder:
        """Change how much rope the agent gets: plan | manual | automatic.

        Recorded in the diary rather than only on the row. ``automatic`` means
        outbound actions stop asking anyone, and "who turned that off, and when"
        has to be answerable from the order itself — the diary is where anyone
        reconstructing what happened actually looks.
        """
        if mode not in WORK_ORDER_MODES:
            raise WorkOrderValidationError(f"mode must be one of: {', '.join(WORK_ORDER_MODES)}")
        wo = await self.get_work_order(wo_id)
        if wo.mode == mode:
            return wo
        previous, wo.mode = wo.mode, mode
        await self._repo.add_entry(
            WorkOrderEntry(
                work_order_id=wo.id,
                role=_HUMAN_LANE,
                text=f"Mode changed from {previous} to {mode}.",
            )
        )
        await self._repo.flush()
        return wo

    async def start_approved_plan(
        self,
        wo_id: uuid.UUID,
        *,
        summary: str,
        actor_profile_id: uuid.UUID | None = None,
        ignore_run_id: uuid.UUID | None = None,
    ) -> None:
        """Queue the run that carries out a plan a person has just approved.

        A fresh run rather than continuing the planning one: that run's transcript
        is a research session, and the thing worth carrying forward is the approved
        plan, not how it was arrived at.
        """
        wo = await self.get_work_order(wo_id)
        await self._dispatch(
            wo,
            actor_profile_id=actor_profile_id,
            task=(
                f"{_brief(wo)}\n\n"
                "--- Your plan, which has been approved ---\n"
                f"{summary}\n\n"
                "Carry it out. The task list on this work order is that plan — work "
                "through it and mark each step as you go."
            ),
            ignore_run_id=ignore_run_id,
        )
        await self._repo.flush()

    async def set_review_level(self, wo_id: uuid.UUID, level: str) -> WorkOrder:
        """How big a board this order convenes. Recorded, like the mode: turning
        review down is a decision someone should be able to find later."""
        from api.services.agents.review_board import REVIEW_LEVELS

        if level not in REVIEW_LEVELS:
            raise WorkOrderValidationError(f"review_level must be one of: {', '.join(REVIEW_LEVELS)}")
        wo = await self.get_work_order(wo_id)
        if wo.review_level == level:
            return wo
        previous, wo.review_level = wo.review_level, level
        await self._repo.add_entry(
            WorkOrderEntry(
                work_order_id=wo.id,
                role=_HUMAN_LANE,
                text=f"Review level changed from {previous} to {level}.",
            )
        )
        await self._repo.flush()
        return wo

    async def attach_documents(
        self,
        wo_id: uuid.UUID,
        document_ids: list[uuid.UUID],
        *,
        kind: str = "input",
        actor_profile_id: uuid.UUID | None = None,
    ) -> list[WorkOrderArtifact]:
        """Link existing documents to this order.

        Ignores ids that are not documents in this org rather than failing the
        whole reply: a message with one bad attachment should still be delivered,
        and the diary shows what did attach.
        """
        if not document_ids:
            return []
        from api.repositories.document import DocumentRepository

        docs = DocumentRepository(self._session, self._org_id)
        repo = WorkOrderArtifactRepository(self._session, self._org_id)
        attached: list[WorkOrderArtifact] = []
        for document_id in document_ids:
            document = await docs.get(document_id)
            if document is None:
                continue
            # A Document has no filename or mime of its own: an uploaded file's
            # object key is `<org>/<document_key>/<filename>`, and a document made
            # from text has no object at all. Both are captured onto the artifact
            # row so the record survives the document being deleted.
            key = document.document_url or ""
            name = key.rsplit("/", 1)[-1] if key else document.title
            attached.append(
                await repo.attach(
                    wo_id,
                    document_id,
                    kind=kind,
                    filename=name,
                    mime=mime_for(name),
                    size=document.size_bytes,
                )
            )
        return attached

    async def list_artifacts(self, wo_id: uuid.UUID) -> list[tuple[WorkOrderArtifact, Any]]:
        await self.get_work_order(wo_id)
        return await WorkOrderArtifactRepository(self._session, self._org_id).list_for(wo_id)

    async def detach_artifact(self, wo_id: uuid.UUID, artifact_id: uuid.UUID) -> None:
        """Unlink an artifact. The document itself is left alone — attaching to the
        wrong order should be undoable without destroying the work."""
        await self.get_work_order(wo_id)
        if not await WorkOrderArtifactRepository(self._session, self._org_id).detach(artifact_id):
            raise WorkOrderNotFoundError(f"Artifact {artifact_id} not found")

    async def reply(
        self,
        wo_id: uuid.UUID,
        text: str,
        *,
        actor_profile_id: uuid.UUID | None = None,
        document_ids: list[uuid.UUID] | None = None,
    ) -> WorkOrder:
        """Record a person's reply on the order and hand it to the agent.

        Agents end runs with questions — sometimes because they were told to ask,
        sometimes just conversationally — and a finished run has nothing listening.
        Without this the only reply anyone could make was filing a second work
        order, which loses everything already in the diary.
        """
        message = text.strip()
        if not message and not document_ids:
            # A reply that is only an attachment is a real reply — "here, look at
            # this" is a whole message. Only an empty one with nothing attached is
            # refused.
            raise WorkOrderValidationError("A reply cannot be empty")
        wo = await self.get_work_order(wo_id)
        # Read the diary before adding the reply: the brief states the reply
        # separately, and a transcript ending in it would say it twice.
        history = (await self.list_entries_page(wo.id, limit=_REPLY_CONTEXT_ENTRIES)).entries
        attached = await self.attach_documents(
            wo.id, document_ids or [], kind="input", actor_profile_id=actor_profile_id
        )
        body = "\n".join(part for part in (message, _attachment_lines(attached)) if part)
        await self._repo.add_entry(WorkOrderEntry(work_order_id=wo.id, role=_HUMAN_LANE, text=body))
        await self._repo.flush()

        # A reply is a contribution to the record whatever state the order is in;
        # it only *starts* an agent on an order that is already under way. Replying
        # to a draft or a finished order must not quietly restart it.
        if wo.assigned_agent_id is None or wo.status != _DISPATCH_STATUS:
            return wo
        if await self._has_live_run(wo.id):
            # Delivering into a turn already in flight is the steer problem, not
            # this one. Say so in the diary rather than letting the reply look
            # delivered — the failure this whole method exists to fix.
            await self._repo.add_entry(
                WorkOrderEntry(
                    work_order_id=wo.id,
                    role=_HUMAN_LANE,
                    text=(
                        "Noted while the agent was still working, so it was not delivered "
                        "to the run in progress. Reply again once that run finishes."
                    ),
                )
            )
            await self._repo.flush()
            return wo

        await self._dispatch(
            wo,
            actor_profile_id=actor_profile_id,
            task=_reply_brief(wo, history, body),
            attachment_ids=[a.document_id for a in attached if a.document_id],
        )
        await self._repo.flush()
        return wo

    async def list_tasks(self, wo_id: uuid.UUID) -> list[WorkOrderTask]:
        await self.get_work_order(wo_id)
        return await self._repo.list_tasks(wo_id)

    async def set_tasks(
        self,
        wo_id: uuid.UUID,
        tasks: list[dict],
        *,
        add_delivery_step: bool = False,
    ) -> list[WorkOrderTask]:
        """Replace the plan, optionally ensuring it ends by handing something over.

        ``add_delivery_step`` is set by the planning tool when the order's brief
        promises an output. A plan that produces a report and never says "attach it"
        finishes with the report inside the agent's own transcript, which is the same
        as not having written it. Appended rather than refused: the agent's plan is
        still its plan, it just now owes a delivery, and one extra step it can see is
        better than a rejection it has to guess its way out of.
        """
        await self.get_work_order(wo_id)
        if add_delivery_step and not has_delivery_step([str(t.get("title") or "") for t in tasks]):
            tasks = [*tasks, {"title": DELIVERY_TASK_TITLE, "sort_order": len(tasks)}]
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
        replaced = await self._repo.replace_tasks(wo_id, models)
        for task in replaced:
            if task.status == "blocked":
                await self.report_blocked(wo_id, task)
        # Re-planning is how an order most often stops being blocked: the checklist is
        # rewritten around the obstacle. The tasks the old alert named do not even
        # exist any more, so leaving it open asks for help with work that is gone.
        await self.clear_blocked_alert(wo_id, replaced)
        return replaced

    async def update_task_status(
        self,
        wo_id: uuid.UUID,
        key: str,
        status: str,
        *,
        agent: Agent | None = None,
        run_id: uuid.UUID | None = None,
        evidence: str | None = None,
    ) -> WorkOrderTask:
        """Move one task, and tell a person when the move is into ``blocked``.

        Blocking is the one status change that is not progress — it is the agent
        saying it cannot continue — and until this it raised nothing at all. An order
        could go from running to nine-of-nine-blocked in a single turn and the only
        trace was the checklist itself, which nobody is watching at 2am. The order's
        own stall sweeper eventually noticed, but only after the run ended, and its
        notification went to an inbox with no badge.

        Raises ``WorkOrderValidationError`` for an unknown key or status so callers
        (the tool, the API) render one message rather than each inventing their own.
        """
        if status not in WORK_ORDER_TASK_STATUSES:
            raise WorkOrderValidationError(f"status must be one of: {', '.join(WORK_ORDER_TASK_STATUSES)}")
        tasks = await self.list_tasks(wo_id)
        target = next((t for t in tasks if t.key.lower() == key.strip().lower()), None)
        if target is None:
            # Name the keys that exist: a caller that guessed one has no other way to
            # find the real one, and would otherwise abandon the update.
            available = ", ".join(t.key for t in tasks) or "none"
            raise WorkOrderValidationError(f"No task with key '{key}'. Keys on this work order: {available}.")
        if status == "done":
            await self._require_evidence(wo_id, target, tasks, evidence)
            await self._gate_acceptance(wo_id, target, tasks)
        was, target.status = target.status, status
        await self._repo.flush()
        if status == "done" and evidence:
            await self._repo.add_entry(
                WorkOrderEntry(
                    work_order_id=wo_id,
                    agent_id=agent.id if agent else None,
                    agent_run_id=run_id,
                    role=agent.name if agent else _SYSTEM_LANE,
                    text=f"{_DONE_MARKER} {target.key} — {evidence}",
                )
            )
        if status == "blocked" and was != "blocked":
            await self.report_blocked(wo_id, target, agent=agent, run_id=run_id)
        elif was == "blocked" and status != "blocked":
            await self.clear_blocked_alert(wo_id, tasks)
        return target

    async def _require_evidence(
        self,
        wo_id: uuid.UUID,
        target: WorkOrderTask,
        tasks: Sequence[WorkOrderTask],
        evidence: str | None,
    ) -> None:
        """Refuse a ``done`` that nothing backs up.

        ``done`` used to be a string an agent wrote about itself, and no code path
        anywhere could disagree. Seen live: nine steps marked done, an adversarial
        review board passed, and the thing actually asked for — open this website and
        audit it — was never attempted. The delivered work was a document about how one
        would build a crawler. Nothing in the system could tell the difference, because
        an agent's output is prose about work, and prose about work looks exactly like
        work.

        Two rules, both borrowed from a definition-of-done that already works:

        * **Say what you produced.** A sentence naming the output and where it is. A
          model that must write "produced X, attached as Y" is markedly less willing to
          claim a step it did not take than one that need only write "done".
        * **The last step cannot close on an empty order.** When every step is done and
          the order has no attachment at all, the checklist is the only evidence that
          anything happened — which is the failure this exists to catch.

        Deliberately not a per-task artifact requirement: plenty of real steps ("agree
        the scope with the filer") produce no file, and a rule that demanded one would
        be routed around by attaching junk.
        """
        said = (evidence or "").strip()
        if len(said) < _MIN_EVIDENCE_CHARS:
            raise WorkOrderValidationError(
                f"Marking {target.key} done needs 'evidence': one concrete sentence saying what you "
                "produced and where it is — 'fetched robots.txt and 42 pages, CSV attached as "
                "crawl.csv', not 'completed the task'. If the step produced nothing to point at, it "
                "is not done: mark it blocked or carried, and say why."
            )
        others_done = all(t.status in ("done", "carried") for t in tasks if t.key != target.key)
        if not others_done:
            return
        if await self._has_deliverable(wo_id):
            return
        wo = await self.get_work_order(wo_id)
        # Only an order that owes a *file* is held to one. "Check out our SEO and tell
        # me what you think" is answered by an answer — demanding an attachment there
        # would make an agent produce a document nobody asked for, purely to satisfy
        # a check. The plan having a delivery step counts too: the agent said it would.
        if wants_deliverable(_brief(wo)) or has_delivery_step([t.title for t in tasks]):
            raise WorkOrderValidationError(
                f"{target.key} is the last open step, but this work order has nothing attached — no "
                "document, no file, no artifact. A finished order whose only evidence is its own "
                "checklist is the failure this check exists to catch. Attach the deliverable with "
                "attach_document (create it first if you must), or leave this step open and escalate "
                "saying what stopped you producing one."
            )

    async def _gate_acceptance(self, wo_id: uuid.UUID, target: WorkOrderTask, tasks: Sequence[WorkOrderTask]) -> None:
        """The last question: does any of this answer what was asked?

        Runs once, on the step that closes the order, because that is the only moment
        the full delivery exists and the only moment refusing it costs nothing already
        spent. See :mod:`api.services.agents.acceptance` for why the auditor is given
        the original request and the result and nothing else.
        """
        if self._settings is None:
            return
        if not all(t.status in ("done", "carried") for t in tasks if t.key != target.key):
            return
        wo = await self.get_work_order(wo_id)
        verdict = await check_acceptance(self._session, self._org_id, wo, self._settings)
        if verdict.ok:
            if not verdict.checked:
                # A skip must never read as a pass. Silence here is how "the auditor
                # was never configured" becomes "the auditor approved it".
                logger.info("acceptance not checked for work order %s", wo_id)
            return
        await self._repo.add_entry(
            WorkOrderEntry(
                work_order_id=wo_id,
                role=_SYSTEM_LANE,
                text=f"{_ACCEPTANCE_MARKER} {verdict.gap}",
            )
        )
        await create_notification(
            self._session,
            self._org_id,
            kind="escalation",
            title=f"“{wo.title}” finished without answering the request",
            body=(
                f"{verdict.gap}\n\nEvery step is complete, but the delivered work does not answer what "
                "was asked. Nothing has been lost — the order is still open. Reply on it to redirect "
                "the agents, or close it yourself if the auditor is wrong."
            ),
            work_order_id=wo_id,
            recipient_role="org_admin",
            settings=self._settings,
        )
        if not self._settings.agent_acceptance_enforce:
            # Report-only mode, for trying the auditor on a live org before letting it
            # refuse anything.
            return
        raise WorkOrderValidationError(
            f"This step closes the order, but the delivered work does not answer the request: "
            f"{verdict.gap} Re-read the work order's own description — not the task list, which may "
            "have drifted from it — and either deliver what was asked, or leave this step open and "
            "escalate saying why it cannot be delivered. A person has been told either way."
        )

    async def _has_deliverable(self, wo_id: uuid.UUID) -> bool:
        """Has anything been attached to this order that a person could open?"""
        row = (
            await self._session.execute(
                select(WorkOrderArtifact.id).where(
                    WorkOrderArtifact.org_id == self._org_id,
                    WorkOrderArtifact.work_order_id == wo_id,
                    WorkOrderArtifact.kind == "output",
                )
            )
        ).first()
        return row is not None

    async def clear_blocked_alert(self, wo_id: uuid.UUID, tasks: Sequence[WorkOrderTask] | None = None) -> None:
        """Retract the "is blocked" alert once nothing on this order is blocked.

        The alert says a person is needed before the order can continue. When the
        agent unblocks the step itself, that stops being true — but the alert stayed
        unresolved, so it kept asking for help that was no longer wanted. Seen live: a
        step was blocked and marked done sixteen seconds later, and the alert outlived
        both. Every stale alert costs the next real one some of its credibility.

        Only when the *last* blocked step clears: an order with five blocked steps and
        one unblocked still needs the same person for the same reason.
        """
        remaining = tasks if tasks is not None else await self.list_tasks(wo_id)
        if any(t.status == "blocked" for t in remaining):
            return
        wo = await self.get_work_order(wo_id)
        await self._session.execute(
            update(AgentNotification)
            .where(
                AgentNotification.org_id == self._org_id,
                AgentNotification.work_order_id == wo_id,
                AgentNotification.title == f"“{wo.title}” is blocked",
                AgentNotification.status != "resolved",
            )
            .values(status="resolved")
        )

    async def report_blocked(
        self,
        wo_id: uuid.UUID,
        task: WorkOrderTask,
        *,
        agent: Agent | None = None,
        run_id: uuid.UUID | None = None,
    ) -> None:
        """Record a blocked step in the diary and raise it once per order.

        Per order, not per task: an agent that hits a missing capability usually
        blocks every remaining step in the same turn, and nine notifications for one
        cause is how a person learns to dismiss them unread. The diary keeps the
        detail — every blocked step writes a line — while the notification fires on
        the first one and stays quiet until someone resolves it and it happens again.
        """
        wo = await self.get_work_order(wo_id)
        title = f"“{wo.title}” is blocked"
        # Throttled on the notification rather than the diary: resolving the alert is
        # a person saying "seen it", and an order that blocks *again* afterwards is
        # news. A diary-based throttle would silence this order forever after one line.
        outstanding = (
            await self._session.execute(
                select(AgentNotification.id).where(
                    AgentNotification.org_id == self._org_id,
                    AgentNotification.work_order_id == wo_id,
                    AgentNotification.title == title,
                    AgentNotification.status != "resolved",
                )
            )
        ).first()
        await self._repo.add_entry(
            WorkOrderEntry(
                work_order_id=wo_id,
                agent_id=agent.id if agent else None,
                agent_run_id=run_id,
                role=_SYSTEM_LANE,
                text=f"{_BLOCKED_MARKER} {task.key} — {task.title}",
            )
        )
        if outstanding is not None:
            return
        tasks = await self.list_tasks(wo_id)
        blocked = [t for t in tasks if t.status == "blocked"]
        await create_notification(
            self._session,
            self._org_id,
            kind="escalation",
            title=title,
            body=(
                f"{agent.name if agent else 'An agent'} marked {task.key} — {task.title} — as blocked"
                f"{f' ({len(blocked)} of {len(tasks)} steps blocked)' if len(blocked) > 1 else ''}. "
                "The work order has the reason; it needs a person before it can continue."
            ),
            work_order_id=wo_id,
            run_id=run_id,
            recipient_role="org_admin",
            settings=self._settings,
        )

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

    async def flush_tasks(self) -> None:
        """Persist in-place task edits (a status change on a loaded row)."""
        await self._repo.flush()

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
