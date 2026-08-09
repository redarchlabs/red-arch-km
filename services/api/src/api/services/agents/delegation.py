"""Delegation & coordination primitives — the agent↔agent protocol, enforced by
the ``supervisor_id`` org chart.

* delegate_task  — supervisor → **direct report only**; queues a child run.
* escalate       — report → its supervisor (or a human at the apex); notifies.
* consult_peer   — cross-tree, **advisory-target only**; blocks for the answer.
* reply_to_peer  — the other half of a consult: the advisor's answer.
* request_review — report → supervisor; records a review request (a WO gate).
* ask_human      — blocks for a person's typed answer.

Three of these queue a child ``AgentRun`` or file an inbox item and return
immediately. ``consult_peer`` and ``ask_human`` do not return at all: they park the
calling run (see :mod:`api.services.agents.questions`) and their answer arrives
later as the result of the very call that blocked. That is the difference between
sending a question and *asking* one — an agent that only files a notification can
never use what comes back.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.agent_run import AgentRun
from api.models.work_order import WorkOrderEntry
from api.repositories.agent import AgentRepository
from api.repositories.agent_questions import AgentQuestionRepository
from api.repositories.agent_run import AgentRunRepository
from api.repositories.work_order import WorkOrderRepository
from api.services.agents import questions
from api.services.agents.notify import create_notification
from api.services.agents.runtime import RunFinished, RunParked
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
    actor_user_id: uuid.UUID | None = None,
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
        # The report acts on the delegator's behalf, so it inherits the delegator's
        # actor — and with it, exactly that person's knowledge-base reach. Without
        # this a delegated child has no actor at all, which would let work handed
        # down the org chart read more than the person who started it.
        actor_user_id=actor_user_id,
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
            ctx.session,
            ctx.org_id,
            ctx.agent,
            target,
            task,
            run_id=ctx.run_id,
            work_order_id=ctx.work_order_id,
            actor_user_id=ctx.actor_user_id,
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
        ctx.session,
        ctx.org_id,
        kind="escalation",
        title=f"Escalation from {ctx.agent.name}",
        body=reason,
        run_id=ctx.run_id,
        work_order_id=ctx.work_order_id,
        recipient_role=None if supervisor else "org_admin",
        settings=ctx.settings,
    )
    await _diary(ctx, f"Escalated: {reason}")
    return {"escalated_to": supervisor.name if supervisor else "human reviewer", "status": "notified"}


def _consult_brief(asker: str, question: str) -> str:
    """The task a consult run wakes up to. It has to say how to answer, because
    ``reply_to_peer`` is the only exit that gets the advice back to the asker."""
    return (
        f"{asker} has consulted you.\n\nQUESTION: {question}\n\n"
        "Answer from your own area of expertise, researching first if you need to. "
        "When you have an answer, call reply_to_peer with it — that is the only way "
        f"{asker} receives it, and it ends your run. Your answer is advice: "
        f"{asker} decides what to do with it."
    )


async def _consult_peer(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Ask an advisory agent a question and BLOCK until it answers.

    Raises :class:`RunParked` — the calling run suspends and resumes with the
    peer's answer as this call's result.
    """
    target = str(args.get("agent") or "").strip()
    question = str(args.get("question") or "").strip()
    if not target or not question:
        return {"error": "Both 'agent' and 'question' are required"}

    # Routing is checked before runnability so the model gets the actionable error
    # ("that agent cannot be consulted") rather than an environmental one.
    peer = await resolve_agent(ctx.session, ctx.org_id, target)
    if peer is None:
        return {"error": f"Unknown peer: {target}"}
    if peer.id == ctx.agent.id:
        return {"error": "You cannot consult yourself"}
    if peer.kind != "advisory":
        return {"error": "You may only consult advisory agents"}
    if not peer.enabled:
        return {"error": f"{peer.name} is not currently enabled"}
    if ctx.run_id is None or not ctx.tool_call_id:
        # Nothing to park: the interactive console runs tools outside a run, so
        # there is no suspended turn for an answer to come back to.
        return {"error": "consult_peer is only available inside an agent run"}

    run = await AgentRunRepository(ctx.session, ctx.org_id).get_run(ctx.run_id)
    if run is not None and run.trigger == "consult":
        # Depth cap. Two advisors that each consult the other would otherwise queue
        # runs forever, and each hop costs a full LLM run.
        return {"error": "A consult may not itself consult. Answer from what you know, or escalate."}

    peer_run = await AgentRunRepository(ctx.session, ctx.org_id).create_run(
        agent_id=peer.id,
        provider=peer.provider,
        model=peer.model,
        trigger="consult",
        input={"task": _consult_brief(ctx.agent.name, question)},
        parent_run_id=ctx.run_id,
        work_order_id=ctx.work_order_id,
        # The advisor answers on the asker's behalf, so it reads with the asker's
        # entitlement — never wider.
        actor_user_id=ctx.actor_user_id,
        status="queued",
        label=f"Consult from {ctx.agent.name}: {question[:60]}",
    )
    row = await questions.create_question(
        ctx.session,
        ctx.org_id,
        run_id=ctx.run_id,
        tool_call_id=ctx.tool_call_id,
        asked_by_agent_id=ctx.agent.id,
        question=question,
        audience="agent",
        target_agent_id=peer.id,
        peer_run_id=peer_run.id,
        work_order_id=ctx.work_order_id,
    )
    await _diary(ctx, f"Consulted {peer.name}: {question}")
    raise RunParked("consult", {"question_id": str(row.id), "peer": peer.name, "peer_run_id": str(peer_run.id)})


async def _reply_to_peer(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Answer the consult this run was created for, then end the run."""
    answer = str(args.get("answer") or "").strip()
    if not answer:
        return {"error": "'answer' is required"}
    if ctx.run_id is None:
        return {"error": "reply_to_peer is only available inside an agent run"}

    row = await AgentQuestionRepository(ctx.session, ctx.org_id).pending_for_peer_run(ctx.run_id)
    if row is None:
        return {"error": "No one is waiting on you — reply_to_peer only answers a consult."}

    outcome = await questions.record_answer(
        ctx.session,
        ctx.org_id,
        row,
        answer=answer,
        by_agent_id=ctx.agent.id,
        answered_by=ctx.agent.name,
    )
    await _diary(ctx, f"Answered the consult: {answer}")
    if not outcome.resumed:
        # The asker gave up (cancelled, timed out) while this run was thinking. The
        # answer is recorded, but say so plainly rather than implying it landed.
        return {"answered": False, "reason": "The agent that asked is no longer waiting for an answer."}
    raise RunFinished("done", {"output": {"answer": answer}})


async def _ask_human(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Ask a person a question and BLOCK until they type an answer.

    Distinct from an approval: an approval is a yes/no on an action this agent has
    already chosen, and the runtime raises it on its own. This is the agent
    deciding it is missing something only a person can supply.
    """
    question = str(args.get("question") or "").strip()
    context = str(args.get("context") or "").strip() or None
    if not question:
        return {"error": "'question' is required"}
    if ctx.run_id is None or not ctx.tool_call_id:
        return {"error": "ask_human is only available inside an agent run"}

    row = await questions.create_question(
        ctx.session,
        ctx.org_id,
        run_id=ctx.run_id,
        tool_call_id=ctx.tool_call_id,
        asked_by_agent_id=ctx.agent.id,
        question=question,
        audience="human",
        context=context,
        work_order_id=ctx.work_order_id,
    )
    await create_notification(
        ctx.session,
        ctx.org_id,
        kind="question",
        title=f"{ctx.agent.name} has a question for you",
        body=f"{question}\n\n{context}" if context else question,
        run_id=ctx.run_id,
        work_order_id=ctx.work_order_id,
        recipient_role="org_admin",
        settings=ctx.settings,
    )
    await _diary(ctx, f"Asked a human: {question}")
    raise RunParked("question", {"question_id": str(row.id), "question": question})


async def _request_review(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return {"error": "'summary' is required"}
    supervisor = await _supervisor(ctx)
    await create_notification(
        ctx.session,
        ctx.org_id,
        kind="review",
        title=f"Review requested by {ctx.agent.name}",
        body=summary,
        run_id=ctx.run_id,
        work_order_id=ctx.work_order_id,
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
    description=(
        "Ask an advisory agent (any team) a question and WAIT for their answer. "
        "Their reply comes back as the result of this call. Advice, not instruction — "
        "you decide what to do with it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Name or id of an advisory agent."},
            "question": {
                "type": "string",
                "description": "Include the context they need — they cannot see your conversation.",
            },
        },
        "required": ["agent", "question"],
    },
    category=Category.ESCALATE,
    handler=_consult_peer,
)

REPLY_TO_PEER = ToolSpec(
    name="reply_to_peer",
    description=(
        "Answer the agent who consulted you. Only works when your run was started by "
        "a consult; it delivers your answer and ends your run."
    ),
    parameters={
        "type": "object",
        "properties": {"answer": {"type": "string", "description": "Your advice, in full."}},
        "required": ["answer"],
    },
    category=Category.ESCALATE,
    handler=_reply_to_peer,
    terminal=True,
)

ASK_HUMAN = ToolSpec(
    name="ask_human",
    description=(
        "Ask a person a question and WAIT for their typed answer, which comes back as "
        "the result of this call. Use it when you are missing a fact, a preference, or "
        "a judgement call only a person can make — not to get permission for an action "
        "you have already decided on (that gate is automatic). Your run pauses until "
        "they reply, so ask only when you genuinely cannot proceed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "One specific, answerable question."},
            "context": {
                "type": "string",
                "description": "What you are working on and why you are stuck — they lack your context.",
            },
        },
        "required": ["question"],
    },
    category=Category.ESCALATE,
    handler=_ask_human,
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
    return [DELEGATE_TASK, ESCALATE, CONSULT_PEER, REPLY_TO_PEER, REQUEST_REVIEW, ASK_HUMAN]
