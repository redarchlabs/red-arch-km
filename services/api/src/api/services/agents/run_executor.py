"""Background execution of agent runs — the worker's unit of work.

The worker beat calls the internal ``/agents/advance-runs`` endpoint, which uses
this executor to claim queued runs (cross-org, ``FOR UPDATE SKIP LOCKED``) and
drive each with the shared :func:`run_agent_loop`. Unlike the interactive console,
the worker path uses the parking approval strategy: an ASK verdict records an
``AgentApproval`` and suspends the run (``waiting``) until a human resolves it.

Runs on the privileged session with explicit ``org_id`` scoping in every repo
(the same discipline the run tools already use), so cross-org claiming is safe.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.config import Settings
from api.dependencies import get_redis_client
from api.models.agent_run import AgentApproval, AgentRun
from api.models.work_order import WorkOrder, WorkOrderEntry
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.repositories.agent_run_messages import AgentRunMessageRepository
from api.services.agents import lifecycle
from api.services.agents.authority import Posture, available_tools, posture_for
from api.services.agents.live.activity import RunActivityPublisher
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import ToolCallRequest
from api.services.agents.llm.routing import provider_for
from api.services.agents.notify import create_notification
from api.services.agents.prompts import build_system_prompt
from api.services.agents.runtime import RunCancelled, RunFinished, RunParked, run_agent_loop
from api.services.agents.tools.loader import load_agent_tools
from api.services.agents.tools.spec import ToolContext, ToolSpec
from api.services.agents.work_order_service import WorkOrderService

logger = logging.getLogger(__name__)

# A stall notice in the diary, and the lane it is filed under. Text rather than a
# column so the whole story of an order stays in the one place people read.
_STALL_MARKER = "⚠️ Stalled:"
_STALL_ROLE = "system"


class AgentRunExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def advance(self, session: AsyncSession, limit: int = 10) -> dict[str, int]:
        """Claim + drive a batch of queued runs, after re-bubbling stale waits.

        RLS scope is set per transaction (SET LOCAL resets on each commit): the
        cross-org backstop + claim run with the bypass GUC on; each run then
        executes downgraded to app_user, scoped to its own org, so RLS is a real
        per-run backstop even if application-level org_id scoping had a bug.
        """
        await db_scope.enter_bypass(session)
        reclaimed = await self._reclaim_stale(session, limit)
        await session.commit()
        await db_scope.enter_bypass(session)
        reminded = await self._backstop(session, limit)
        await session.commit()
        await db_scope.enter_bypass(session)
        stalled = await self._stalled_orders(session, limit)
        await session.commit()
        await db_scope.enter_bypass(session)
        claimed = await self._claim(session, limit)
        await session.commit()
        executed = errors = 0
        for run_id, org_id in claimed:
            try:
                await db_scope.enter_tenant(session, org_id)
                await self._execute_one(session, org_id, run_id)
                await session.commit()
                executed += 1
            except Exception:  # noqa: BLE001 - one bad run must not stop the sweep
                logger.exception("agent run %s failed", run_id)
                await session.rollback()
                await db_scope.enter_tenant(session, org_id)
                await self._mark_error(session, org_id, run_id)
                await session.commit()
                errors += 1
        return {
            "claimed": len(claimed),
            "executed": executed,
            "errors": errors,
            "reminded": reminded,
            "reclaimed": reclaimed,
            "stalled": stalled,
        }

    async def _reclaim_stale(self, session: AsyncSession, limit: int) -> int:
        """Lease recovery: a ``running`` run whose heartbeat went stale was orphaned
        by a dead worker (deploy, OOM). Requeue it once; a second expiry means the
        task itself is the problem — finalize as error so a linked workflow token
        escalates instead of waiting forever."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.agent_run_lease_ttl_seconds)
        rows = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(AgentRun.status == "running", AgentRun.last_activity_at < cutoff)
                    .order_by(AgentRun.last_activity_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for run in rows:
            attempts = int((run.input or {}).get("_lease_requeues") or 0) if isinstance(run.input, dict) else 0
            if attempts >= 1:
                await lifecycle.finalize_run(
                    session, run.org_id, run, status="error", error="worker lost (lease expired twice)"
                )
                continue
            run.input = {**(run.input or {}), "_lease_requeues": attempts + 1}
            run.status = "queued"
            run.last_activity_at = datetime.now(UTC)
        return len(rows)

    async def _backstop(self, session: AsyncSession, limit: int) -> int:
        """Re-bubble runs that have been ``waiting`` longer than the escalation
        timeout, so a stalled approval/escalation isn't silently forgotten. Throttled
        by bumping ``last_activity_at``, so each stale run re-notifies at most once per
        timeout window rather than every sweep."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.agent_escalation_timeout_seconds)
        rows = (
            (
                await session.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.status == "waiting",
                        AgentRun.last_activity_at < cutoff,
                        # Workflow-triggered runs: the step's timer boundary owns
                        # the SLA and the run view surfaces the pending approval —
                        # a second reminder stream would just split the queue.
                        AgentRun.trigger != "workflow",
                    )
                    .order_by(AgentRun.last_activity_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for run in rows:
            await create_notification(
                session,
                run.org_id,
                kind="escalation",
                title="Reminder: an agent run is still waiting for you",
                body=f"Run {run.id} has been waiting ({run.wait_kind}) past the timeout.",
                run_id=run.id,
                work_order_id=run.work_order_id,
                recipient_role="org_admin",
                settings=self._settings,
            )
            run.last_activity_at = datetime.now(UTC)
        return len(rows)

    async def _stalled_orders(self, session: AsyncSession, limit: int) -> int:
        """Work orders whose agents have all stopped with the job unfinished.

        A run's ``done`` only ever meant the model stopped calling tools — not that
        the work happened. So an order could sit ``in_progress`` at 17%, with every
        run terminal and nobody working, looking exactly like one in flight. Every
        other stall in this system surfaces (a waiting run, a pending approval);
        this one was invisible, which made it the easiest to lose.

        Detection only. Nothing is restarted: an agent that stopped may have been
        right to, and quietly re-running it would spend money on a decision a person
        has not seen yet.
        """
        live = (
            select(AgentRun.work_order_id)
            .where(AgentRun.status.in_(("queued", "running", "waiting")), AgentRun.work_order_id.is_not(None))
            .scalar_subquery()
        )
        rows = (
            (
                await session.execute(
                    select(WorkOrder)
                    .where(
                        WorkOrder.status == "in_progress",
                        WorkOrder.id.not_in(live),
                        # At least one run: an order nobody has dispatched is not
                        # stalled, it is human work or waiting to be started.
                        WorkOrder.id.in_(
                            select(AgentRun.work_order_id).where(AgentRun.work_order_id.is_not(None)).scalar_subquery()
                        ),
                    )
                    .order_by(WorkOrder.updated_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

        notified = 0
        for wo in rows:
            service = WorkOrderService(session, wo.org_id)
            tasks = await service.list_tasks(wo.id)
            outstanding = [t for t in tasks if t.status not in ("done", "carried")]
            if not outstanding:
                continue
            if await self._already_flagged(session, wo):
                continue
            done = len(tasks) - len(outstanding)
            await create_notification(
                session,
                wo.org_id,
                kind="escalation",
                title=f"“{wo.title}” has stopped with work outstanding",
                body=(
                    f"Every agent on this work order has finished, but {len(outstanding)} of "
                    f"{len(tasks)} tasks are still open ({done} done). "
                    f"Still outstanding: {', '.join(t.key for t in outstanding[:8])}."
                ),
                work_order_id=wo.id,
                recipient_role="org_admin",
                settings=self._settings,
            )
            await service.add_entry(
                wo.id,
                role=_STALL_ROLE,
                text=(
                    f"{_STALL_MARKER} No agent is working this order and "
                    f"{len(outstanding)} of {len(tasks)} tasks are still open."
                ),
            )
            notified += 1
        return notified

    async def _already_flagged(self, session: AsyncSession, wo: WorkOrder) -> bool:
        """Has this stall already been reported since the last run finished?

        Throttled against the newest run rather than a timestamp, so an order that
        is restarted and stalls *again* is reported again — the same discipline as
        ``_backstop``'s activity bump, expressed in the diary because a work order
        has no heartbeat of its own.
        """
        newest = (
            await session.execute(select(func.max(AgentRun.created_at)).where(AgentRun.work_order_id == wo.id))
        ).scalar()
        query = select(WorkOrderEntry.id).where(
            WorkOrderEntry.work_order_id == wo.id, WorkOrderEntry.text.like(f"{_STALL_MARKER}%")
        )
        if newest is not None:
            query = query.where(WorkOrderEntry.created_at > newest)
        return (await session.execute(query)).first() is not None

    async def _claim(self, session: AsyncSession, limit: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
        rows = (
            await session.execute(
                select(AgentRun.id, AgentRun.org_id)
                .where(AgentRun.status == "queued")
                .order_by(AgentRun.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claimed = [(r.id, r.org_id) for r in rows]
        if claimed:
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id.in_([rid for rid, _ in claimed]))
                .values(status="running", started_at=func.now(), last_activity_at=func.now())
            )
        return claimed

    async def _mark_error(self, session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        run = await AgentRunRepository(session, org_id).get_run(run_id)
        if run is not None:
            await lifecycle.finalize_run(session, org_id, run, status="error", error="execution failed")

    async def _execute_one(self, session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        run_repo = AgentRunRepository(session, org_id)
        run = await run_repo.get_run(run_id)
        if run is None or run.status != "running":
            return
        agent = await AgentRepository(session, org_id).get(run.agent_id) if run.agent_id else None
        if agent is None or not agent.enabled:
            await lifecycle.finalize_run(session, org_id, run, status="error", error="agent missing or disabled")
            return
        key = await resolve_provider_key(session, org_id, agent.provider, self._settings)
        if not key:
            await lifecycle.finalize_run(
                session, org_id, run, status="error", error=f"no key for provider {agent.provider}"
            )
            return

        linkage = run.input.get("workflow") if isinstance(run.input, dict) else None
        linkage = linkage if isinstance(linkage, dict) else None

        ctx = ToolContext(
            session=session,
            org_id=org_id,
            settings=self._settings,
            agent=agent,
            actor_user_id=run.actor_user_id,
            run_id=run.id,
            work_order_id=run.work_order_id,
        )
        # Resolve the posture before building the tool list, not after: a plan-mode
        # run that is *offered* tools it will be denied burns turns proposing
        # actions that can never happen.
        from api.models.org import Org
        from api.models.work_order import WorkOrder

        org = await session.get(Org, org_id)
        org_posture = (getattr(org, "agent_autonomy", None) or "high_touch") if org else "high_touch"
        work_order = await session.get(WorkOrder, run.work_order_id) if run.work_order_id else None
        autonomy = posture_for(work_order, org_posture)

        specs = available_tools(
            agent,
            await load_agent_tools(session, org_id, agent, self._settings, actor_user_id=run.actor_user_id),
            autonomy=autonomy,
        )
        if autonomy == Posture.PLAN_ONLY:
            # The exit from plan mode, and only offered there: submitting a plan on
            # an order already being worked would mean nothing.
            from api.services.agents.tools.plan_mode import SUBMIT_PLAN

            specs = [*specs, SUBMIT_PLAN]
        if linkage is not None:
            # Workflow mode: the completion contract comes in; un-gated egress
            # goes out (web_research's query string leaves the org without an
            # approval stop — prompt-injected record text must not reach it).
            from api.services.agents.tools.bridge import workflow_bridge_specs

            if not linkage.get("allow_web_research"):
                specs = [s for s in specs if s.name != "web_research"]
            raw_schema = linkage.get("output_schema")
            schema: dict[str, Any] = raw_schema if isinstance(raw_schema, dict) else {}
            specs = [*specs, *workflow_bridge_specs(schema)]

        # Resume a parked turn (human approved) or start fresh from the task.
        resume = run.input.get("resume") if isinstance(run.input, dict) else None
        if resume:
            messages = list(resume.get("messages") or [])
            resume_tool_calls = [ToolCallRequest(**tc) for tc in resume.get("pending") or []]
            approved_names = set(resume.get("approved") or [])
            resume_answers = dict(resume.get("answers") or {})
        else:
            task = str(run.input.get("task") or run.input.get("message") or "").strip() or "Proceed."
            # A work-order run is watched through a task list the agent has to fill
            # in; without the title it does not know it is on one.
            system = build_system_prompt(
                agent,
                work_order_title=work_order.title if work_order else None,
                plan_only=autonomy == Posture.PLAN_ONLY,
            )
            if linkage is not None:
                from api.services.agents.tools.bridge import workflow_system_addendum, wrap_workflow_task

                system = f"{system}\n\n{workflow_system_addendum()}"
                task = wrap_workflow_task(task)
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": task},
            ]
            resume_tool_calls = None
            approved_names = set()
            resume_answers = {}

        # Steps stay the durable record; the publisher is a live view bolted beside
        # it. Deltas are published and NOT persisted — the assistant message is
        # already stored whole, and one row per token would multiply the step table
        # for a transcript that is only interesting while it is being written.
        publisher = RunActivityPublisher(
            get_redis_client(self._settings),
            org_id,
            run.id,
            agent_name=agent.name,
            work_order_id=run.work_order_id,
        )

        async def emit(event: dict[str, Any]) -> None:
            await self._persist_event(run_repo, run.id, event)
            await publisher.publish(event)

        async def continue_check() -> bool:
            # Column select bypasses the identity map; READ COMMITTED sees an
            # external cancel as soon as it commits.
            return await run_repo.current_status(run.id) == "running"

        async def steer() -> list[str]:
            """Messages a person queued for this run, taken exactly once.

            Committed immediately: the drain marks them delivered, and if the turn
            it feeds them into then fails, they must not silently reappear and be
            acted on twice.
            """
            texts = await AgentRunMessageRepository(session, org_id).drain(run.id)
            if texts:
                await session.commit()
                await db_scope.enter_tenant(session, org_id)
            return texts

        async def drive(
            msgs: list[dict[str, Any]],
            resume_calls: list[ToolCallRequest] | None,
            answers: dict[str, Any] | None = None,
        ):
            return await run_agent_loop(
                provider=provider_for(self._settings, agent.model, key),
                agent=agent,
                model=agent.model,
                messages=msgs,
                specs=specs,
                ctx=ctx,
                emit=emit,
                max_iterations=self._settings.agent_max_iterations,
                temperature=(agent.params or {}).get("temperature"),
                max_tokens=(agent.params or {}).get("max_tokens"),
                reasoning_effort=(agent.params or {}).get("reasoning_effort"),
                approval_strategy=self._make_strategy(session, org_id, run, approved_names),
                resume_tool_calls=resume_calls,
                resume_answers=answers,
                autonomy=autonomy,
                steer=steer,
                continue_check=continue_check,
            )

        try:
            # Answers apply only to the turn that was parked; the corrective nudge
            # below is a fresh turn and must not inherit them.
            try:
                result = await drive(messages, resume_tool_calls, resume_answers)
            finally:
                # Whatever ends the run — finished, parked, cancelled, error — the
                # last few buffered tokens are the ones explaining why. Losing them
                # to a cancelled flush timer is the one moment they matter most.
                await publisher.close()
            if linkage is not None and not result.truncated:
                # Prose is not a completion for a workflow step: one corrective
                # nudge, then (below) escalate rather than pretend success.
                first = result
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The workflow step is NOT complete. Call complete_task with the required "
                            "structured fields, or escalate_task with a reason if you cannot."
                        ),
                    }
                )
                result = await drive(messages, None)
                result.prompt_tokens += first.prompt_tokens
                result.completion_tokens += first.completion_tokens
                result.total_tokens += first.total_tokens
            elif run.work_order_id is not None and not result.truncated:
                nudge = await self._unfinished_work_nudge(session, org_id, run)
                if nudge:
                    # Observed live: an agent read the task list, asked three
                    # questions, wrote a paragraph and stopped — leaving five of six
                    # tasks pending. Its run was "done", which only ever meant the
                    # model stopped calling tools. One corrective turn, the same
                    # shape as the workflow contract above, so stopping is at least
                    # a decision rather than a drift.
                    first = result
                    messages.append({"role": "user", "content": nudge})
                    result = await drive(messages, None)
                    result.prompt_tokens += first.prompt_tokens
                    result.completion_tokens += first.completion_tokens
                    result.total_tokens += first.total_tokens
        except RunCancelled:
            # The canceller owns the terminal state; committing here persists the
            # transcript steps of the turns that DID run, nothing else.
            logger.info("agent run %s cancelled externally; stopping without finalize", run.id)
            return
        except RunFinished as finished:
            if isinstance(run.input, dict) and "resume" in run.input:
                run.input = {k: v for k, v in run.input.items() if k != "resume"}
            if finished.status == "done":
                run.output = dict(finished.payload.get("output") or {})
                await run_repo.add_step(run.id, kind="assistant", content={"completed": True, "output": run.output})
                await lifecycle.finalize_run(
                    session,
                    org_id,
                    run,
                    status="done",
                    prompt_tokens=finished.prompt_tokens,
                    completion_tokens=finished.completion_tokens,
                    total_tokens=finished.total_tokens,
                )
            else:
                reason = str(finished.payload.get("reason") or "agent escalated")
                await run_repo.add_step(run.id, kind="escalation", content={"reason": reason})
                await lifecycle.finalize_run(
                    session,
                    org_id,
                    run,
                    status="escalated",
                    error=reason,
                    prompt_tokens=finished.prompt_tokens,
                    completion_tokens=finished.completion_tokens,
                    total_tokens=finished.total_tokens,
                )
            return
        except RunParked as parked:
            pending = parked.pending or []
            # Carry forward only answers whose call has still not executed. A call
            # that already consumed its answer is recorded in ``messages``; keeping
            # its entry would re-inject the same answer if the provider ever reuses
            # the id, while dropping a still-pending one would re-ask the human.
            still_pending = {str(p.get("id")) for p in pending}
            run.input = {
                **(run.input or {}),
                "resume": {
                    "messages": parked.messages or messages,
                    "pending": pending,
                    "approved": sorted(approved_names),
                    "answers": {k: v for k, v in resume_answers.items() if k in still_pending},
                },
            }
            await run_repo.mark_waiting(
                run,
                parked.wait_kind,
                prompt_tokens=parked.prompt_tokens,
                completion_tokens=parked.completion_tokens,
                total_tokens=parked.total_tokens,
            )
            if parked.wait_kind == "approval":
                # ask_human and consult_peer notify from their own handlers, where
                # the question text lives; only the authority gate lands here.
                await create_notification(
                    session,
                    org_id,
                    kind="approval",
                    title=f"{agent.name} needs approval",
                    body=str(parked.payload.get("tool")),
                    run_id=run.id,
                    work_order_id=run.work_order_id,
                    recipient_role="org_admin",
                    settings=self._settings,
                )
            return

        # Clear resume state once the run completes.
        if isinstance(run.input, dict) and "resume" in run.input:
            run.input = {k: v for k, v in run.input.items() if k != "resume"}
        await run_repo.add_step(run.id, kind="assistant", content={"content": result.final_content})

        if linkage is not None:
            # The loop ended without complete_task/escalate_task even after the
            # nudge — never map that to "completed".
            reason = (
                "iteration budget exhausted before complete_task"
                if result.truncated
                else "agent finished without calling complete_task"
            )
            await lifecycle.finalize_run(
                session,
                org_id,
                run,
                status="escalated",
                error=reason,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
            )
            return

        won = await lifecycle.finalize_run(
            session,
            org_id,
            run,
            status="done",
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        )
        if won:
            await self._signal_parent(session, org_id, run, agent.name, result.final_content)

    async def _unfinished_work_nudge(self, session: AsyncSession, org_id: uuid.UUID, run: AgentRun) -> str | None:
        """One reminder for an agent about to stop with its checklist unfinished.

        ``None`` when there is nothing to say — no work order, no task list, or the
        list is complete — so a run that genuinely finished is not asked to justify
        itself, and neither is one on an order nobody planned.

        Deliberately not an instruction to keep working: the agent may have a good
        reason to stop, and being told to carry on regardless is how an agent
        invents work. It is asked to *account* for the gap, which either produces
        the work or produces a reason a person can read.
        """
        if run.work_order_id is None:
            return None
        service = WorkOrderService(session, org_id)
        tasks = await service.list_tasks(run.work_order_id)
        outstanding = [t for t in tasks if t.status not in ("done", "carried")]
        if not tasks or not outstanding:
            return None
        names = ", ".join(f"{t.key} ({t.title[:60]})" for t in outstanding[:8])
        return (
            "Before you stop: this work order's checklist is not finished. Still "
            f"outstanding: {names}.\n"
            "If you have done any of it, mark it with update_work_order_task now — "
            "the percentage a person sees comes from those calls, not from your reply. "
            "If you are blocked, say on what with ask_human. If a step should not be "
            "done on this order, mark it 'carried' so it stops counting against you. "
            "If you are genuinely finished, call request_review and say why the "
            "remaining steps were not needed."
        )

    def _make_strategy(self, session: AsyncSession, org_id: uuid.UUID, run: AgentRun, approved_names: set[str]):
        """Approval strategy: auto-approve already-approved tools; otherwise record an
        approval and park the run for a human."""

        async def strategy(spec: ToolSpec, args: dict[str, Any]) -> bool:
            if spec.name in approved_names:
                return True
            approval = AgentApproval(
                run_id=run.id, tool_name=spec.name, arguments=args, status="pending", org_id=org_id
            )
            session.add(approval)
            await session.flush()
            raise RunParked("approval", {"approval_id": str(approval.id), "tool": spec.name})

        return strategy

    async def _signal_parent(
        self, session: AsyncSession, org_id: uuid.UUID, run: AgentRun, agent_name: str, summary: str
    ) -> None:
        """A delegated child finished — record it on the work order for the supervisor."""
        if run.work_order_id is None:
            return
        note = (summary or "").strip()
        await WorkOrderService(session, org_id).add_entry(
            run.work_order_id,
            text=f"{agent_name} completed its task. {note[:400]}",
            agent_id=run.agent_id,
            agent_run_id=run.id,
            role=agent_name,
        )

    async def _persist_event(self, run_repo: AgentRunRepository, run_id: uuid.UUID, event: dict) -> None:
        kind = event.get("type")
        if kind in ("tool_call", "tool_result", "usage"):
            # Lease heartbeat: at least once per turn (usage) and per tool round-trip,
            # so long multi-turn runs never look orphaned to _reclaim_stale.
            await run_repo.heartbeat(run_id)
        if kind == "tool_call":
            await run_repo.add_step(
                run_id, kind="tool_call", name=event.get("name"), content={"arguments": event.get("arguments")}
            )
        elif kind == "tool_result":
            # The FULL result, always. The transcript's copy may be elided, and this
            # step is what read_run_detail hands back when it is — keyed by call_id,
            # which is the handle the elision leaves behind.
            await run_repo.add_step(
                run_id,
                kind="tool_result",
                name=event.get("name"),
                content={"result": event.get("result"), "call_id": event.get("call_id")},
            )
        elif kind == "compaction":
            # Recorded so the run's history explains its own gap: a reader who sees
            # the summary can see what it replaced and how much it saved.
            await run_repo.add_step(
                run_id,
                kind="compaction",
                content={
                    "summary": event.get("summary"),
                    "folded": event.get("folded"),
                    "before_chars": event.get("before_chars"),
                    "after_chars": event.get("after_chars"),
                },
            )
        elif kind == "approval_required":
            await run_repo.add_step(
                run_id, kind="approval_required", name=event.get("name"), content={"arguments": event.get("arguments")}
            )
        # delta/usage/done are not persisted as steps on the worker path
