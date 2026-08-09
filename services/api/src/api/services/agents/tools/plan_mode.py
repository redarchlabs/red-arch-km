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

from typing import Any

from api.services.agents.runtime import RunFinished
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

# The mode an approved plan lands in. Not ``automatic``: approving a plan says the
# plan is right, which is not the same as saying nobody needs to see what happens
# while it is carried out.
_APPROVED_MODE = "manual"


async def _submit_plan(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return {"error": "This run is not attached to a work order, so there is no plan to submit."}
    summary = str(args.get("summary") or "").strip()
    if not summary:
        return {"error": "'summary' is required — state the plan you want approved."}

    from api.services.agents.work_order_service import WorkOrderService

    service = WorkOrderService(ctx.session, ctx.org_id)
    tasks = await service.list_tasks(ctx.work_order_id)

    # Reaching the handler at all means a human already approved: the gate parks
    # the run before this runs, and a rejection never gets here.
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
    if "error" in result:
        # A refusal the agent can act on, not the end of the run.
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
    always_ask=True,
    terminal=True,
)
