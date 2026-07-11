"""Delegation & coordination primitives — the KM2 port of the reference's four
agent↔agent tools, enforced by the ``supervisor_id`` org chart.

* delegate_task  — supervisor → **direct report only**; queues a child run.
* escalate       — report → its supervisor (or a human at the apex); notifies.
* consult_peer   — cross-tree, **advisory-target only**, non-binding.
* request_review — report → supervisor; records a review request (a WO gate).

delegate_task creates a queued child ``AgentRun`` the worker sweep drives; the
others record intent + an inbox notification (full park/resume lands with the
approval/escalation inbox).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.work_order import WorkOrderEntry
from api.repositories.agent import AgentRepository
from api.repositories.agent_run import AgentRunRepository
from api.repositories.work_order import WorkOrderRepository
from api.services.agents.notify import create_notification
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec


class DelegationError(Exception):
    pass


async def resolve_agent(session: AsyncSession, org_id: uuid.UUID, ref: str) -> Agent | None:
    repo = AgentRepository(session, org_id)
    try:
        return await repo.get(uuid.UUID(str(ref)))
    except (ValueError, TypeError):
        return await repo.get_by_name(str(ref))


async def _diary(ctx: ToolContext, text: str) -> None:
    if ctx.work_order_id is None:
        return
    await WorkOrderRepository(ctx.session, ctx.org_id).add_entry(
        WorkOrderEntry(
            work_order_id=ctx.work_order_id,
            agent_id=ctx.agent.id,
            agent_run_id=ctx.run_id,
            role=ctx.agent.name,
            text=text,
        )
    )


async def delegate(
    session: AsyncSession,
    org_id: uuid.UUID,
    caller: Agent,
    target_ref: str,
    task: str,
    *,
    run_id: uuid.UUID | None,
    work_order_id: uuid.UUID | None,
) -> AgentRun:
    """Queue a child run for a DIRECT report. Raises on a non-report target."""
    target = await resolve_agent(session, org_id, target_ref)
    if target is None:
        raise DelegationError(f"Unknown target agent: {target_ref}")
    if target.supervisor_id != caller.id:
        raise DelegationError(f"'{caller.name}' may only delegate to its direct reports")
    return await AgentRunRepository(session, org_id).create_run(
        agent_id=target.id,
        provider=target.provider,
        model=target.model,
        trigger="delegation",
        input={"task": task},
        parent_run_id=run_id,
        work_order_id=work_order_id,
        status="queued",
        label=f"Delegated: {task[:80]}",
    )


# --- tool handlers ---------------------------------------------------------


async def _delegate_task(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("agent") or "").strip()
    task = str(args.get("task") or "").strip()
    if not target or not task:
        return {"error": "Both 'agent' and 'task' are required"}
    try:
        run = await delegate(
            ctx.session, ctx.org_id, ctx.agent, target, task,
            run_id=ctx.run_id, work_order_id=ctx.work_order_id,
        )
    except DelegationError as exc:
        return {"error": str(exc)}
    await _diary(ctx, f"Delegated to {target}: {task}")
    return {"delegated_to": target, "child_run_id": str(run.id), "status": "queued"}


async def _supervisor(ctx: ToolContext) -> Agent | None:
    if ctx.agent.supervisor_id is None:
        return None
    return await AgentRepository(ctx.session, ctx.org_id).get(ctx.agent.supervisor_id)


async def _escalate(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    reason = str(args.get("reason") or "").strip()
    if not reason:
        return {"error": "'reason' is required"}
    supervisor = await _supervisor(ctx)
    await create_notification(
        ctx.session, ctx.org_id, kind="escalation",
        title=f"Escalation from {ctx.agent.name}", body=reason,
        run_id=ctx.run_id, work_order_id=ctx.work_order_id,
        recipient_role=None if supervisor else "org_admin",
        settings=ctx.settings,
    )
    await _diary(ctx, f"Escalated: {reason}")
    return {"escalated_to": supervisor.name if supervisor else "human reviewer", "status": "notified"}


async def _consult_peer(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("agent") or "").strip()
    question = str(args.get("question") or "").strip()
    if not target or not question:
        return {"error": "Both 'agent' and 'question' are required"}
    peer = await resolve_agent(ctx.session, ctx.org_id, target)
    if peer is None:
        return {"error": f"Unknown peer: {target}"}
    if peer.kind != "advisory":
        return {"error": "You may only consult advisory agents"}
    await create_notification(
        ctx.session, ctx.org_id, kind="review",
        title=f"{ctx.agent.name} consulted {peer.name}", body=question,
        run_id=ctx.run_id, work_order_id=ctx.work_order_id,
    )
    return {"consulted": peer.name, "status": "sent"}


async def _request_review(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return {"error": "'summary' is required"}
    supervisor = await _supervisor(ctx)
    await create_notification(
        ctx.session, ctx.org_id, kind="review",
        title=f"Review requested by {ctx.agent.name}", body=summary,
        run_id=ctx.run_id, work_order_id=ctx.work_order_id,
        recipient_role=None if supervisor else "org_admin",
        settings=ctx.settings,
    )
    await _diary(ctx, f"Requested review: {summary}")
    return {"review_requested_from": supervisor.name if supervisor else "human reviewer", "status": "pending"}


DELEGATE_TASK = ToolSpec(
    name="delegate_task",
    description="Delegate a task to one of your DIRECT reports (queues a run for them).",
    parameters={
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Name or id of a direct report."},
            "task": {"type": "string", "description": "What they should do."},
        },
        "required": ["agent", "task"],
    },
    category=Category.DELEGATE,
    handler=_delegate_task,
    side_effecting=True,
)

ESCALATE = ToolSpec(
    name="escalate",
    description="Escalate a blocker to your supervisor (or a human if you are at the top).",
    parameters={
        "type": "object",
        "properties": {"reason": {"type": "string"}, "context": {"type": "string"}},
        "required": ["reason"],
    },
    category=Category.ESCALATE,
    handler=_escalate,
)

CONSULT_PEER = ToolSpec(
    name="consult_peer",
    description="Ask an advisory agent (any team) a non-binding question.",
    parameters={
        "type": "object",
        "properties": {"agent": {"type": "string"}, "question": {"type": "string"}},
        "required": ["agent", "question"],
    },
    category=Category.ESCALATE,
    handler=_consult_peer,
)

REQUEST_REVIEW = ToolSpec(
    name="request_review",
    description="Ask your supervisor to review your work before it is considered done.",
    parameters={
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
    category=Category.ESCALATE,
    handler=_request_review,
)


def delegation_tool_specs() -> list[ToolSpec]:
    return [DELEGATE_TASK, ESCALATE, CONSULT_PEER, REQUEST_REVIEW]
