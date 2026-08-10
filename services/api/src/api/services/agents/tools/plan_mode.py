"""The way out of plan mode: submit the plan, and let approving it start the work.

Plan mode without this was a dead end. The agent researched, wrote a task list,
and stopped — and the only way to actually do the work was for a person to change
the mode by hand and start the order again, with the agent's reasoning left behind
in a finished run.

So it follows the shape people already know from Claude Code: work read-only,
present the plan, and let the human's approval be the thing that releases
execution. ``submit_plan`` is ``always_ask``, so calling it parks the run and puts
the plan in the approval queue. Approve and the handler runs — the order moves to
``manual`` and a fresh run picks the work up. Reject and the agent gets an ordinary
"denied" tool result, still in plan mode, free to revise and submit again.
"""

from __future__ import annotations

import uuid
from typing import Any

from api.services.agents.runtime import RunFinished
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

# The mode an approved plan lands in. Not ``automatic``: approving a plan says the
# plan is right, which is not the same as saying nobody needs to see what happens
# while it is carried out.
_APPROVED_MODE = "manual"


_GATE = "plan"


async def _clear_the_board(ctx: ToolContext, work_order: Any, summary: str, tasks: list[Any]) -> dict[str, Any] | None:
    """Run the plan past its review board. ``None`` means cleared — carry on.

    Returns a tool result when the author still has work to do: findings to
    address, or an acknowledgement that the board is now sitting. Parks the run
    (via ``convene``) while the board reads.
    """
    from api.services.agents import review_board as rb
    from api.services.agents import review_gate

    digest = rb.fingerprint(summary)
    state = await review_gate.status(ctx, work_order.id, _GATE, digest)
    if state["passed"]:
        return None

    boards = await review_gate._org_boards(ctx)
    seats = review_gate.board_for(work_order, boards, author=ctx.agent.name if ctx.agent else None)
    if not seats:
        return None  # review_level 'none', or an org with no board configured

    outcome = state["outcome"]
    if state["same_submission"] and outcome.settled:
        entries = state["entries"]
        if outcome.approved:
            _mark(ctx, work_order.id, f"🏛️ {rb.PASSED} {_GATE} ({digest}) — {', '.join(outcome.verdicts)}")
            return None
        if rb.rounds_run(entries, _GATE) >= rb.MAX_ROUNDS:
            # The cap exists so a reviewer that never softens and an author that
            # never satisfies it cannot trade runs forever. The objections go to
            # the human with the plan rather than being dropped.
            _mark(ctx, work_order.id, f"🏛️ {rb.RELEASED} {_GATE} ({digest}) — unresolved: {', '.join(outcome.failed)}")
            return None
        # Say how bounded this is. Observed live: an agent handed a second round of
        # objections stopped resubmitting and asked a person instead, so the cap —
        # which only fires on the next submit_plan — never ran and the plan sat in
        # plan mode. An agent that knows the process ends has a reason to finish it.
        remaining = rb.MAX_ROUNDS - rb.rounds_run(entries, _GATE)
        return {
            "review": "changes requested",
            "failed": outcome.failed,
            "findings": dict(outcome.verdicts),
            "review_rounds_remaining": remaining,
            "note": (
                "Address these and call submit_plan again with a revised summary; "
                "resubmitting the same text will not re-open the review. "
                f"{remaining} review round(s) remain, after which this goes to a person "
                "with any unresolved objections attached."
            ),
        }

    await review_gate.convene(
        ctx,
        work_order,
        gate=_GATE,
        submission=summary,
        tasks="\n".join(f"{t.key} {t.title}" for t in tasks),
        seats=seats,
    )
    return None  # unreachable: convene parks the run


async def _human_approved(ctx: ToolContext) -> bool:
    """Has a person already approved this run's plan?

    Read from ``agent_approvals`` rather than held in memory: the approval is
    settled by a different request, in a different process, long after this
    handler first ran.
    """
    from sqlalchemy import select

    from api.models.agent_run import AgentApproval

    row = (
        await ctx.session.execute(
            select(AgentApproval.id).where(
                AgentApproval.run_id == ctx.run_id,
                AgentApproval.org_id == ctx.org_id,
                AgentApproval.tool_name == "submit_plan",
                AgentApproval.status == "approved",
            )
        )
    ).first()
    return row is not None


async def _ask_the_human(ctx: ToolContext, summary: str) -> None:
    """Put the plan in the approval queue and park. Never returns."""
    from api.models.agent_run import AgentApproval
    from api.services.agents.runtime import RunParked

    approval = AgentApproval(
        run_id=ctx.run_id,
        tool_name="submit_plan",
        arguments={"summary": summary[:4000]},
        status="pending",
        org_id=ctx.org_id,
    )
    ctx.session.add(approval)
    await ctx.session.flush()
    raise RunParked("approval", {"approval_id": str(approval.id), "tool": "submit_plan"})


def _mark(ctx: ToolContext, wo_id: uuid.UUID, text: str) -> None:
    from api.models.work_order import WorkOrderEntry

    ctx.session.add(WorkOrderEntry(work_order_id=wo_id, org_id=ctx.org_id, role="review", text=text))


async def _submit_plan(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return {"error": "This run is not attached to a work order, so there is no plan to submit."}
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return {"error": "'summary' is required — state the plan you want approved."}

    from api.services.agents.work_order_service import WorkOrderService

    service = WorkOrderService(ctx.session, ctx.org_id, ctx.settings)
    tasks = await service.list_tasks(ctx.work_order_id)
    work_order = await service.get_work_order(ctx.work_order_id)

    # Peer review comes BEFORE the human, so what a person is asked to approve
    # arrives with its objections already attached rather than being the first
    # thing anyone has read.
    gated = await _clear_the_board(ctx, work_order, summary, tasks)
    if gated is not None:
        return gated

    # Only now is a person asked. Raised here rather than by the authority gate's
    # ``always_ask``, because that fires *before* the handler — which would put the
    # human first and the board second, so someone would be approving a plan nobody
    # had reviewed. That is the whole point of the ordering.
    if not await _human_approved(ctx):
        await _ask_the_human(ctx, summary)
    await service.add_entry(
        ctx.work_order_id,
        agent_id=ctx.agent.id if ctx.agent else None,
        agent_run_id=ctx.run_id,
        role=ctx.agent.name if ctx.agent else None,
        text=f"Plan approved. Starting work.\n\n{summary}",
    )
    await service.set_mode(ctx.work_order_id, _APPROVED_MODE)
    # Continue in a new run rather than here: this one was started to plan, and its
    # transcript is a research session. The executing run gets a clean brief built
    # from the approved plan.
    await service.start_approved_plan(
        ctx.work_order_id,
        summary=summary,
        actor_profile_id=ctx.actor_user_id,
        ignore_run_id=ctx.run_id,
    )
    return {
        "approved": True,
        "mode": _APPROVED_MODE,
        "tasks": [{"key": t.key, "title": t.title} for t in tasks],
        "note": "The plan was approved and a run has been queued to carry it out.",
    }


async def _handler(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    result = await _submit_plan(ctx, args)
    # Only an approved plan ends the run. A refusal the agent can act on — a blank
    # summary, or a board asking for changes — is an ordinary tool result, so the
    # agent revises and submits again. Ending the run on "changes requested" would
    # close the order on the reviewers' objections.
    if not result.get("approved"):
        return result
    raise RunFinished("plan approved", result)


SUBMIT_PLAN = ToolSpec(
    name="submit_plan",
    description=(
        "Submit your finished plan for approval. This work order is in PLAN MODE, so this "
        "is how the work actually starts: state what you intend to do and why, and a person "
        "approves or rejects it. Write the task list with set_work_order_tasks first — the "
        "summary explains the plan, the task list IS the plan."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "The plan in your own words: what you will do, in what order, what you "
                    "need, and anything you are unsure about."
                ),
            }
        },
        "required": ["summary"],
    },
    category=Category.PLAN,
    handler=_handler,
    always_allowed=True,
    # NOT always_ask: that fires before the handler, which would ask a person to
    # approve a plan the board has not read yet. The handler raises the approval
    # itself, after the review.
    terminal=True,
)
