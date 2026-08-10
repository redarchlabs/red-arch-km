"""Delegation & coordination primitives — the agent↔agent protocol, enforced by
the ``supervisor_id`` org chart.

* delegate_task  — supervisor → **direct report only**; queues a child run.
* escalate       — report → its supervisor; queues a run for them (a human at the apex).
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


# How many names a routing error lists before it truncates. Nothing in the system
# prompt or the tool schema tells an agent who its colleagues are, so these errors
# are the only place the roster is ever named — but a large org would otherwise
# push the model's actual work out of the window with a wall of names.
HINT_NAME_CAP = 20

# How far an escalation may travel up the chart before it stops climbing and asks a
# person instead. Escalation only ever moves up, so the chart's own depth normally
# ends it — this bounds the pathological case (a supervisor_id cycle drawn by hand)
# and the merely useless one, where five agents in a row each pass the same blocker
# along because none of them can fix it either. Carried in the run's input rather
# than derived from the chart, so the count survives however the chain is shaped.
MAX_ESCALATION_HOPS = 4

# Questions to a person per run. Two is a genuine blocker plus one follow-up;
# past that an agent is interviewing rather than working, and every question ends
# the run until somebody answers it.
MAX_HUMAN_QUESTIONS_PER_RUN = 2


async def resolve_agent(session: AsyncSession, org_id: uuid.UUID, ref: str) -> Agent | None:
    repo = AgentRepository(session, org_id)
    try:
        return await repo.get(uuid.UUID(str(ref)))
    except (ValueError, TypeError):
        return await repo.get_by_name(str(ref))


def _name_list(agents: list[Agent]) -> str:
    names = [a.name for a in agents]
    if len(names) <= HINT_NAME_CAP:
        return ", ".join(names)
    return f"{', '.join(names[:HINT_NAME_CAP])}, and {len(names) - HINT_NAME_CAP} more"


async def _consultable_hint(session: AsyncSession, org_id: uuid.UUID, caller: Agent) -> str:
    """Name the agents ``consult_peer`` would accept.

    Without this a wrong name is a dead end: a model has no way to discover the
    real one, so it abandons the consult and answers from memory — which reads as
    a considered choice rather than the routing failure it is.
    """
    peers = await AgentRepository(session, org_id).list_consultable(exclude_id=caller.id)
    if not peers:
        return "There are no advisory agents in this organization to consult."
    return f"Advisory agents you can consult: {_name_list(peers)}."


async def _reports_hint(session: AsyncSession, org_id: uuid.UUID, caller: Agent) -> str:
    """Name the agents ``delegate_task`` would accept — the same dead end, one
    level up the org chart."""
    reports = await AgentRepository(session, org_id).list_direct_reports(caller.id)
    if not reports:
        return f"'{caller.name}' has no direct reports to delegate to."
    return f"Direct reports of '{caller.name}': {_name_list(reports)}."


async def routable_colleagues(
    session: AsyncSession,
    org_id: uuid.UUID,
    agent: Agent,
) -> tuple[list[Agent], list[Agent]]:
    """``(direct reports, consultable advisors)`` — who this agent can route work to.

    Feeds the system prompt. Disabled reports are dropped: naming one costs the model
    a turn discovering the run it queued will never execute.
    """
    repo = AgentRepository(session, org_id)
    reports = [a for a in await repo.list_direct_reports(agent.id) if a.enabled]
    advisors = await repo.list_consultable(exclude_id=agent.id)
    return reports, advisors


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
        raise DelegationError(f"Unknown target agent: {target_ref}. {await _reports_hint(session, org_id, caller)}")
    if target.supervisor_id != caller.id:
        raise DelegationError(
            f"'{caller.name}' may only delegate to its direct reports. {await _reports_hint(session, org_id, caller)}"
        )
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


def _escalation_brief(reporter: str, reason: str, context: str | None) -> str:
    """The task the supervisor's run wakes up to.

    It has to say that the escalation is theirs to resolve, because a supervisor
    handed a bare problem statement tends to restate it and stop. The three named
    moves are the only ones that change anything: a different report, a different
    approach, or a person.
    """
    body = f"{reporter} has escalated a blocker to you.\n\nBLOCKER: {reason}\n"
    if context:
        body += f"\nCONTEXT FROM {reporter}: {context}\n"
    return (
        body + "\nThis is now yours to resolve. "
        f"{reporter} could not get past it, so repeating their approach will fail the same way. "
        "Either delegate the work to a report whose tools can actually do it, find another route "
        "to the same outcome, or — if nothing you can reach will work — escalate to your own "
        "supervisor or ask a human, naming exactly what you need."
    )


async def _escalation_hops(ctx: ToolContext) -> int:
    """How many escalations this run is already downstream of."""
    if ctx.run_id is None:
        return 0
    run = await AgentRunRepository(ctx.session, ctx.org_id).get_run(ctx.run_id)
    if run is None:
        return 0
    return int((getattr(run, "input", None) or {}).get("escalation_hops") or 0)


async def _escalate(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Hand a blocker up the chart — and actually *wake* whoever it lands on.

    This used to write a notification and nothing else. With a supervisor set the
    row was addressed to no role, so no person saw it either: the report believed it
    had escalated, the supervisor never ran, and the order sat still. Observed live —
    an analyst offered "escalate to chief-of-staff for platform access", a person
    picked it, and the escalation went nowhere while every remaining task was marked
    blocked. An escalation that queues no run is a message dropped on the floor.
    """
    reason = str(args.get("reason") or "").strip()
    context = str(args.get("context") or "").strip() or None
    if not reason:
        return {"error": "'reason' is required"}

    supervisor = await _supervisor(ctx)
    hops = await _escalation_hops(ctx)
    # A disabled supervisor is the same dead end as no supervisor: nothing would run.
    handler = supervisor if supervisor is not None and supervisor.enabled else None
    if handler is not None and hops >= MAX_ESCALATION_HOPS:
        handler = None
        reason = f"{reason}\n\n(Escalated {hops} times without resolution; stopping here and asking a person.)"

    child: AgentRun | None = None
    if handler is not None:
        child = await AgentRunRepository(ctx.session, ctx.org_id).create_run(
            agent_id=handler.id,
            provider=handler.provider,
            model=handler.model,
            trigger="escalation",
            input={"task": _escalation_brief(ctx.agent.name, reason, context), "escalation_hops": hops + 1},
            parent_run_id=ctx.run_id,
            work_order_id=ctx.work_order_id,
            # Same rule as delegation: the supervisor picks the work up on behalf of
            # whoever started it, and reads with exactly that person's entitlement.
            actor_user_id=ctx.actor_user_id,
            status="queued",
            label=f"Escalation from {ctx.agent.name}: {reason[:60]}",
        )

    await create_notification(
        ctx.session,
        ctx.org_id,
        kind="escalation",
        title=f"Escalation from {ctx.agent.name}",
        body=reason,
        run_id=ctx.run_id,
        work_order_id=ctx.work_order_id,
        # Only page the admins when no agent is picking this up — otherwise the
        # notification is a record of a hand-off, not a request for help.
        recipient_role=None if handler else "org_admin",
        settings=ctx.settings,
    )
    await _diary(ctx, f"Escalated to {handler.name if handler else 'a human'}: {reason}")
    if handler is None:
        return {"escalated_to": "human reviewer", "status": "notified"}
    return {
        "escalated_to": handler.name,
        "status": "queued",
        "supervisor_run_id": str(child.id) if child is not None else None,
    }


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
        return {"error": f"Unknown peer: {target}. {await _consultable_hint(ctx.session, ctx.org_id, ctx.agent)}"}
    if peer.id == ctx.agent.id:
        return {"error": "You cannot consult yourself"}
    if peer.kind != "advisory":
        return {
            "error": (
                f"'{peer.name}' is a {peer.kind} agent; you may only consult advisory agents. "
                f"{await _consultable_hint(ctx.session, ctx.org_id, ctx.agent)}"
            )
        }
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
    # A consult raised by a review board carries a verdict, and the board is read
    # back from the diary — so the reply has to leave a PASS/FAIL there, not just
    # prose. Ordinary consults are untouched: record_verdict only fires for a
    # question attached to a work order.
    from api.services.agents.review_gate import record_verdict

    await record_verdict(ctx, row, answer)
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

    asked = await AgentQuestionRepository(ctx.session, ctx.org_id).count_for_run(ctx.run_id, audience="human")
    if asked >= MAX_HUMAN_QUESTIONS_PER_RUN:
        # A budget, because the prompt asking for restraint is persuasion and this
        # is not. Observed live: an agent read its task list, asked three questions,
        # wrote a paragraph and stopped, having done none of the work. Each question
        # ends the run until a person answers, so an agent that asks freely converts
        # a job into an interview.
        return {
            "error": (
                f"You have already asked {asked} question(s) on this run, which is the limit. "
                "Proceed on your best assumption and say plainly what you assumed — a person "
                "can correct an assumption, and can do nothing with a run that stopped to ask."
            )
        }

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


_DELIVERY_GATE = "delivery"


async def _peer_review_delivery(ctx: ToolContext, summary: str) -> dict[str, Any] | None:
    """Put a finished deliverable past its board. ``None`` means cleared.

    Silent for everything the board does not cover — a run with no work order, an
    order with review turned off, an org with no board — so this changes nothing
    for the paths that were never meant to be reviewed.
    """
    if ctx.work_order_id is None or ctx.run_id is None:
        return None

    from api.services.agents import review_board as rb
    from api.services.agents import review_gate
    from api.services.agents.work_order_service import WorkOrderService

    service = WorkOrderService(ctx.session, ctx.org_id, ctx.settings)
    work_order = await service.get_work_order(ctx.work_order_id)
    digest = rb.fingerprint(summary)
    state = await review_gate.status(ctx, work_order.id, _DELIVERY_GATE, digest)
    if state["passed"]:
        return None

    boards = await review_gate._org_boards(ctx)
    seats = review_gate.board_for(work_order, boards, author=ctx.agent.name if ctx.agent else None)
    if not seats:
        return None

    outcome = state["outcome"]
    if state["same_submission"] and outcome.settled:
        if outcome.approved or rb.rounds_run(state["entries"], _DELIVERY_GATE) >= rb.MAX_ROUNDS:
            marker = rb.PASSED if outcome.approved else rb.RELEASED
            ctx.session.add(
                WorkOrderEntry(
                    work_order_id=work_order.id,
                    org_id=ctx.org_id,
                    role="review",
                    text=f"🏛️ {marker} {_DELIVERY_GATE} ({digest}) — {', '.join(outcome.verdicts) or 'no verdicts'}",
                )
            )
            await ctx.session.flush()
            return None
        return {
            "review": "changes requested",
            "failed": outcome.failed,
            "findings": dict(outcome.verdicts),
            "note": (
                "Your reviewers are not satisfied. Address this and call request_review "
                "again with a revised summary of what you delivered."
            ),
        }

    tasks = await service.list_tasks(work_order.id)
    await review_gate.convene(
        ctx,
        work_order,
        gate=_DELIVERY_GATE,
        submission=summary,
        tasks="\n".join(f"{t.key} {t.title} [{t.status}]" for t in tasks),
        seats=seats,
    )
    return None  # unreachable: convene parks the run


async def _request_review(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return {"error": "'summary' is required"}

    # A finished deliverable goes past its board before it goes to a person. This
    # is the second half of the same argument as the plan gate: a plan that passed
    # review can still produce a wrong result, and that is where confident-wrong
    # output usually surfaces.
    peer = await _peer_review_delivery(ctx, summary)
    if peer is not None:
        return peer

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
    description=(
        "Escalate a blocker to your supervisor — this starts a run for them, so they will "
        "actually pick it up (a human is notified instead if you are at the top of the chart). "
        "Use it when you cannot get past something with the tools you have."
    ),
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
