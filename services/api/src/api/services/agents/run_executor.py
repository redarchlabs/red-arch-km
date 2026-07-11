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

from api.config import Settings
from api.models.agent_run import AgentApproval, AgentRun
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.services.agents.authority import available_tools
from api.services.agents.llm.keys import resolve_provider_key
from api.services.agents.llm.provider import LLMProvider
from api.services.agents.notify import create_notification
from api.services.agents.prompts import build_system_prompt
from api.services.agents.llm.provider import ToolCallRequest
from api.services.agents.runtime import RunParked, run_agent_loop
from api.services.agents.tools.loader import load_agent_tools
from api.services.agents.tools.spec import ToolContext, ToolSpec
from api.services.agents.work_order_service import WorkOrderService

logger = logging.getLogger(__name__)


class AgentRunExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def advance(self, session: AsyncSession, limit: int = 10) -> dict[str, int]:
        """Claim + drive a batch of queued runs, after re-bubbling stale waits."""
        reminded = await self._backstop(session, limit)
        await session.commit()
        claimed = await self._claim(session, limit)
        await session.commit()
        executed = errors = 0
        for run_id, org_id in claimed:
            try:
                await self._execute_one(session, org_id, run_id)
                await session.commit()
                executed += 1
            except Exception:  # noqa: BLE001 - one bad run must not stop the sweep
                logger.exception("agent run %s failed", run_id)
                await session.rollback()
                await self._mark_error(session, org_id, run_id)
                await session.commit()
                errors += 1
        return {"claimed": len(claimed), "executed": executed, "errors": errors, "reminded": reminded}

    async def _backstop(self, session: AsyncSession, limit: int) -> int:
        """Re-bubble runs that have been ``waiting`` longer than the escalation
        timeout, so a stalled approval/escalation isn't silently forgotten. Throttled
        by bumping ``last_activity_at``, so each stale run re-notifies at most once per
        timeout window rather than every sweep."""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._settings.agent_escalation_timeout_seconds)
        rows = (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.status == "waiting", AgentRun.last_activity_at < cutoff)
                .order_by(AgentRun.last_activity_at)
                .limit(limit)
            )
        ).scalars().all()
        for run in rows:
            await create_notification(
                session, run.org_id, kind="escalation",
                title="Reminder: an agent run is still waiting for you",
                body=f"Run {run.id} has been waiting ({run.wait_kind}) past the timeout.",
                run_id=run.id, work_order_id=run.work_order_id, recipient_role="org_admin",
                settings=self._settings,
            )
            run.last_activity_at = datetime.now(UTC)
        return len(rows)

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
        if run is not None and run.status in ("running", "queued"):
            await AgentRunRepository(session, org_id).finalize_run(run, status="error", error="execution failed")

    async def _execute_one(self, session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
        run_repo = AgentRunRepository(session, org_id)
        run = await run_repo.get_run(run_id)
        if run is None or run.status != "running":
            return
        agent = await AgentRepository(session, org_id).get(run.agent_id) if run.agent_id else None
        if agent is None or not agent.enabled:
            await run_repo.finalize_run(run, status="error", error="agent missing or disabled")
            return
        key = await resolve_provider_key(session, org_id, agent.provider, self._settings)
        if not key:
            await run_repo.finalize_run(run, status="error", error=f"no key for provider {agent.provider}")
            return

        ctx = ToolContext(
            session=session, org_id=org_id, settings=self._settings, agent=agent,
            actor_user_id=run.actor_user_id, run_id=run.id, work_order_id=run.work_order_id,
        )
        specs = available_tools(agent, await load_agent_tools(session, org_id, agent, self._settings))

        # Resume a parked turn (human approved) or start fresh from the task.
        resume = run.input.get("resume") if isinstance(run.input, dict) else None
        if resume:
            messages = list(resume.get("messages") or [])
            resume_tool_calls = [ToolCallRequest(**tc) for tc in resume.get("pending") or []]
            approved_names = set(resume.get("approved") or [])
        else:
            task = str(run.input.get("task") or run.input.get("message") or "").strip() or "Proceed."
            messages = [
                {"role": "system", "content": build_system_prompt(agent)},
                {"role": "user", "content": task},
            ]
            resume_tool_calls = None
            approved_names = set()

        async def emit(event: dict[str, Any]) -> None:
            await self._persist_event(run_repo, run.id, event)

        try:
            result = await run_agent_loop(
                provider=LLMProvider(api_key=key), agent=agent, model=agent.model, messages=messages,
                specs=specs, ctx=ctx, emit=emit, max_iterations=self._settings.agent_max_iterations,
                temperature=(agent.params or {}).get("temperature"),
                max_tokens=(agent.params or {}).get("max_tokens"),
                approval_strategy=self._make_strategy(session, org_id, run, approved_names),
                resume_tool_calls=resume_tool_calls,
            )
        except RunParked as parked:
            run.input = {
                **(run.input or {}),
                "resume": {
                    "messages": parked.messages or messages,
                    "pending": parked.pending or [],
                    "approved": sorted(approved_names),
                },
            }
            await run_repo.mark_waiting(run, parked.wait_kind)
            await create_notification(
                session, org_id, kind="approval",
                title=f"{agent.name} needs approval", body=str(parked.payload.get("tool")),
                run_id=run.id, work_order_id=run.work_order_id, recipient_role="org_admin",
                settings=self._settings,
            )
            return

        # Clear resume state once the run completes.
        if isinstance(run.input, dict) and "resume" in run.input:
            run.input = {k: v for k, v in run.input.items() if k != "resume"}
        await run_repo.add_step(run.id, kind="assistant", content={"content": result.final_content})
        await run_repo.finalize_run(
            run, status="done", prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens, total_tokens=result.total_tokens,
        )
        await self._signal_parent(session, org_id, run, agent.name, result.final_content)

    def _make_strategy(
        self, session: AsyncSession, org_id: uuid.UUID, run: AgentRun, approved_names: set[str]
    ):
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
        if kind == "tool_call":
            await run_repo.add_step(run_id, kind="tool_call", name=event.get("name"), content={"arguments": event.get("arguments")})
        elif kind == "tool_result":
            await run_repo.add_step(run_id, kind="tool_result", name=event.get("name"), content={"result": event.get("result")})
        elif kind == "approval_required":
            await run_repo.add_step(run_id, kind="approval_required", name=event.get("name"), content={"arguments": event.get("arguments")})
        # delta/usage/done are not persisted as steps on the worker path
