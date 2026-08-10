"""Work-order checklist tools — the plan an agent works to, visible to people.

An agent could file a diary entry saying what it intended, but nothing it wrote
became the order's *task list*: that could only be set through the API by a human.
So a coordinator handed a job had no way to break it down where anyone could see
the breakdown, and a work order that fanned out to five delegations showed 0%
complete forever.

These operate on the work order the run already belongs to (``ctx.work_order_id``),
never on an id the model supplies. An agent editing another order's plan is not a
capability anyone asked for, and taking the id from the run makes it impossible
rather than merely discouraged.
"""

from __future__ import annotations

from typing import Any

from api.models.work_order import WORK_ORDER_TASK_STATUSES
from api.services.agents.tools.spec import Category, ToolContext, ToolSpec

# Replacing more than this in one call is a sign the model is restating the plan
# rather than planning; it also keeps one bad turn from writing hundreds of rows.
_MAX_TASKS = 40


def _service(ctx: ToolContext) -> Any:
    from api.services.agents.work_order_service import WorkOrderService

    # Settings are passed so a step blocking here can also leave the app — the
    # in-app inbox alone is what let a blocked order sit unnoticed for five hours.
    return WorkOrderService(ctx.session, ctx.org_id, ctx.settings)


def _no_work_order() -> dict[str, Any]:
    return {
        "error": (
            "This run is not attached to a work order, so it has no task list. "
            "Task tools are only available to a run started from a work order."
        )
    }


async def _set_tasks(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return _no_work_order()
    raw = args.get("tasks")
    if not isinstance(raw, list) or not raw:
        return {"error": "'tasks' must be a non-empty list of task titles"}
    if len(raw) > _MAX_TASKS:
        return {"error": f"Too many tasks ({len(raw)}); plan at most {_MAX_TASKS} steps."}

    titles = [str(t).strip() for t in raw if str(t).strip()]
    if not titles:
        return {"error": "Every task was blank"}

    from api.services.agents.work_order_service import DELIVERY_TASK_TITLE, wants_deliverable

    service = _service(ctx)
    # An order that promises a report, a CSV, a design — anything a person opens —
    # gets a step for handing it over, if the plan does not already have one. A plan
    # that produces a report and never says "attach it" ends with the report inside
    # the agent's own transcript, which is the same as never having written it.
    wo = await service.get_work_order(ctx.work_order_id)
    owed = wants_deliverable(f"{wo.title}\n{wo.body or ''}")

    rejected = await _unworkable(ctx, wo, titles)
    if rejected is not None:
        return rejected

    # Replaces the whole list rather than appending: a plan is a statement of the
    # work as it is now understood, and merging would silently keep steps the
    # agent has just decided against.
    tasks = await service.set_tasks(
        ctx.work_order_id,
        [{"title": title, "sort_order": i} for i, title in enumerate(titles)],
        add_delivery_step=owed,
    )
    added = any(t.title == DELIVERY_TASK_TITLE for t in tasks)
    note = "This replaced the previous plan. Update each task as you finish it."
    if added:
        note += (
            " This order promises something a person will open, so a delivery step was added — "
            "the order cannot be finished with nothing attached to it."
        )
    return {
        "tasks": [{"key": t.key, "title": t.title, "status": t.status} for t in tasks],
        "note": note,
    }


# How many times one run may have a plan sent back. Once. A second opinion from the
# same judge on a plan the same model just rewrote is unlikely to differ, and a
# planner trapped in rework cannot plan at all — which is a worse failure than the
# bad plan, and one the acceptance gate never gets a chance to catch.
_MAX_REWORKS = 1
_REWORKS_KEY = "plan_reworks"


async def _unworkable(ctx: ToolContext, wo: Any, titles: list[str]) -> dict[str, Any] | None:
    """Send the plan back if this org cannot carry it out. See plan_check.py.

    Returned as a tool error rather than raised, so the model re-plans inside the turn
    it planned in — the alternative is a run that ends and a continuation that starts
    from the same brief with none of the reason it was refused.
    """
    from api.services.agents.plan_check import check_plan

    if ctx.settings is None or ctx.extras.get(_REWORKS_KEY, 0) >= _MAX_REWORKS:
        return None
    verdict = await check_plan(
        ctx.session,
        ctx.org_id,
        brief=f"{wo.title}\n{wo.body or ''}".strip(),
        titles=titles,
        settings=ctx.settings,
    )
    if verdict.ok:
        return None
    ctx.extras[_REWORKS_KEY] = ctx.extras.get(_REWORKS_KEY, 0) + 1
    return {
        "error": (
            f"This plan was not saved: {verdict.problem}\n\n"
            "Call set_work_order_tasks again with a plan you can actually finish using the tools "
            "you have. Steps you cannot do are not steps — drop them, or say in the plan that you "
            "will report what could not be checked and why. It is better to answer the request "
            "partially and say what is missing than to plan work nobody here can do."
        )
    }


async def _update_task(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return _no_work_order()
    key = str(args.get("key") or "").strip()
    status = str(args.get("status") or "").strip()
    if not key or not status:
        return {"error": "Both 'key' and 'status' are required"}

    from api.services.agents.work_order_service import WorkOrderValidationError

    service = _service(ctx)
    try:
        # The service owns the transition, because blocking a step has to reach a
        # person and a tool handler is the wrong place to know that.
        target = await service.update_task_status(
            ctx.work_order_id,
            key,
            status,
            agent=ctx.agent,
            run_id=ctx.run_id,
            evidence=str(args.get("evidence") or "").strip() or None,
        )
    except WorkOrderValidationError as exc:
        # A bad key or status is the model's mistake to correct, not a run failure:
        # returned as text so it can try again this turn.
        return {"error": str(exc)}
    tasks = await service.list_tasks(ctx.work_order_id)
    remaining = [t.title for t in tasks if t.status not in ("done", "carried")]
    return {
        "updated": {"key": target.key, "title": target.title, "status": status},
        "progress": service.progress(tasks),
        "remaining": remaining,
    }


async def _list_tasks(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return _no_work_order()
    service = _service(ctx)
    tasks = await service.list_tasks(ctx.work_order_id)
    return {
        "tasks": [{"key": t.key, "title": t.title, "status": t.status} for t in tasks],
        "progress": service.progress(tasks),
    }


SET_WORK_ORDER_TASKS = ToolSpec(
    name="set_work_order_tasks",
    description=(
        "Break this work order into a checklist of steps, REPLACING any existing plan. "
        "Do this first, before starting work: the list is how a person watching sees "
        "what you intend and how far along you are."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered step titles, each one concrete enough to mark done.",
            }
        },
        "required": ["tasks"],
    },
    category=Category.PLAN,
    handler=_set_tasks,
    # NOT side_effecting: that flag means an action that leaves the company, and
    # under the default high_touch posture it forces approval. Writing a plan is
    # internal, and an agent that must ask permission before it can say what it
    # intends to do cannot plan at all.
    side_effecting=False,
)

UPDATE_WORK_ORDER_TASK = ToolSpec(
    name="update_work_order_task",
    description=(
        "Mark one checklist step's status as you go (in_progress, done, blocked, carried). "
        "Percent complete comes from these, so an unupdated list reads as no progress. "
        "Marking a step 'done' REQUIRES 'evidence': what you produced and where it is. A step "
        "that produced nothing anyone can point at is not done — block it or carry it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "The task key, e.g. 'T2'."},
            "status": {
                "type": "string",
                "enum": list(WORK_ORDER_TASK_STATUSES),
                "description": "'carried' means deliberately not doing it on this order.",
            },
            "evidence": {
                "type": "string",
                "description": (
                    "Required for 'done'. One concrete sentence: what you produced and where it "
                    "is — 'fetched robots.txt and crawled 42 pages, CSV attached as crawl.csv'. "
                    "Not 'completed the task'. It is shown to the person watching."
                ),
            },
        },
        "required": ["key", "status"],
    },
    category=Category.PLAN,
    handler=_update_task,
    side_effecting=False,  # internal progress reporting — see set_work_order_tasks
)

LIST_WORK_ORDER_TASKS = ToolSpec(
    name="list_work_order_tasks",
    description="Read this work order's checklist and how much of it is done.",
    parameters={"type": "object", "properties": {}},
    category=Category.READ,
    handler=_list_tasks,
    always_allowed=True,
)


def work_order_task_specs() -> list[ToolSpec]:
    return [SET_WORK_ORDER_TASKS, UPDATE_WORK_ORDER_TASK, LIST_WORK_ORDER_TASKS]
