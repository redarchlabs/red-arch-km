"""Workflow tools — let an agent list and run the KM2 workflows it is granted.

``run_workflow`` reuses the single security-critical entrypoint
``execute_workflow_run`` shared by the internal + public workflow surfaces. The
agent's ``workflow_allowlist`` is the per-agent gate on *which* workflows it may
run (the authority layer already gated whether it may use ``run_workflow`` at all).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException

from api.services.agents.tools.spec import Category, ToolContext, ToolSpec


def _allowed(ctx: ToolContext) -> set[str]:
    return {str(w) for w in (ctx.agent.workflow_allowlist or [])}


async def _list_workflows(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    from api.repositories.workflow import WorkflowRepository

    allow = _allowed(ctx)
    if not allow:
        return {"workflows": []}
    workflows = await WorkflowRepository(ctx.session, ctx.org_id).list_all()
    out = [
        {"id": str(wf.id), "name": wf.name, "description": getattr(wf, "description", None)}
        for wf in workflows
        if str(wf.id) in allow
    ]
    return {"workflows": out}


async def _run_workflow(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from api.repositories.workflow import WorkflowRepository
    from api.schemas.workflow import ManualRunRequest
    from api.services.workflow.manual_run import execute_workflow_run, resolve_published_version

    raw_id = args.get("workflow_id")
    allow = _allowed(ctx)
    if not raw_id or not allow or str(raw_id) not in allow:
        return {"error": "This agent is not permitted to run that workflow"}
    try:
        workflow_id = uuid.UUID(str(raw_id))
    except ValueError:
        return {"error": "invalid workflow_id"}

    wf = await WorkflowRepository(ctx.session, ctx.org_id).get(workflow_id)
    if wf is None:
        return {"error": "workflow not found"}
    try:
        version = await resolve_published_version(ctx.session, ctx.org_id, wf)
    except HTTPException as exc:
        return {"error": str(exc.detail)}

    request = ManualRunRequest(
        operation=str(args.get("operation") or "manual"),
        record_id=None,
        before=None,
        after=None,
        inputs=dict(args.get("inputs") or {}),
    )
    try:
        result = await execute_workflow_run(
            ctx.session, ctx.org_id, wf, version,
            request=request, actor_user_id=ctx.actor_user_id, settings=ctx.settings,
        )
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    return {
        "run_id": str(result.run_id),
        "status": result.status,
        "actions_executed": result.actions_executed,
        "conditions_matched": result.conditions_matched,
        "error": result.error,
    }


LIST_WORKFLOWS = ToolSpec(
    name="list_workflows",
    description="List the workflows this agent is permitted to run.",
    parameters={"type": "object", "properties": {}},
    category=Category.READ,
    handler=_list_workflows,
    always_allowed=True,
)

RUN_WORKFLOW = ToolSpec(
    name="run_workflow",
    description="Run one of the agent's permitted workflows for real with the given inputs.",
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "The workflow id to run."},
            "inputs": {"type": "object", "description": "Input variables the workflow's trigger declares."},
            "operation": {"type": "string", "description": "Usually 'manual'."},
        },
        "required": ["workflow_id"],
    },
    category=Category.EXECUTE,
    handler=_run_workflow,
    side_effecting=True,
)
