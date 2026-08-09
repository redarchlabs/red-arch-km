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

    return WorkOrderService(ctx.session, ctx.org_id)


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

    # Replaces the whole list rather than appending: a plan is a statement of the
    # work as it is now understood, and merging would silently keep steps the
    # agent has just decided against.
    tasks = await _service(ctx).set_tasks(
        ctx.work_order_id, [{"title": title, "sort_order": i} for i, title in enumerate(titles)]
    )
    return {
        "tasks": [{"key": t.key, "title": t.title, "status": t.status} for t in tasks],
        "note": "This replaced the previous plan. Update each task as you finish it.",
    }


async def _update_task(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.work_order_id is None:
        return _no_work_order()
    key = str(args.get("key") or "").strip()
    status = str(args.get("status") or "").strip()
    if not key or not status:
        return {"error": "Both 'key' and 'status' are required"}
    if status not in WORK_ORDER_TASK_STATUSES:
        return {"error": f"status must be one of: {', '.join(WORK_ORDER_TASK_STATUSES)}"}

    service = _service(ctx)
    tasks = await service.list_tasks(ctx.work_order_id)
    target = next((t for t in tasks if t.key.lower() == key.lower()), None)
    if target is None:
        # Name the keys that exist: a model that guessed one has no other way to
        # find the real one, and would otherwise abandon the update.
        available = ", ".join(t.key for t in tasks) or "none"
        return {"error": f"No task with key '{key}'. Keys on this work order: {available}."}

    target.status = status
    await service.flush_tasks()
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
    side_effecting=True,
)

UPDATE_WORK_ORDER_TASK = ToolSpec(
    name="update_work_order_task",
    description=(
        "Mark one checklist step's status as you go (in_progress, done, blocked, carried). "
        "Percent complete comes from these, so an unupdated list reads as no progress."
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
        },
        "required": ["key", "status"],
    },
    category=Category.PLAN,
    handler=_update_task,
    side_effecting=True,
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
