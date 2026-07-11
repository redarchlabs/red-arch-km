"""Execute a workflow's published version on demand.

This is the security-critical core shared by the internal workflows router and the
public ``/api/v1`` workflow surface. Keeping it in one place means both callers
enforce the SAME guards:

* manual (BPMN "none" start) runs validate/coerce caller inputs against the
  declared schema — a caller can only supply variables the author designed;
* entity-bound runs NEVER trust client-supplied record data — with a ``record_id``
  the before/after are reloaded from the real (org+entity-scoped) row, and without
  one any side-effecting action has its record context nulled.

Callers are responsible for loading + authorizing the workflow/version first
(``can_run`` for a user session; the ``workflows:run`` scope for an API key).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.models.workflow import Workflow, WorkflowVersion
from api.repositories.workflow import WorkflowVersionRepository
from api.schemas.workflow import ManualRunRequest, ManualRunResult
from api.services.workflow.actions import SIDE_EFFECTING_ACTIONS
from api.services.workflow.factory import build_dispatch_service
from api.services.workflow.manual_inputs import InputValidationError, coerce_inputs, is_manual_trigger


async def resolve_published_version(
    session: AsyncSession, org_id: uuid.UUID, workflow: Workflow
) -> WorkflowVersion:
    """Return the workflow's active PUBLISHED version, or raise 409.

    Shared by the internal workflows router and the ``/api/v1`` workflow surface so
    the "has no published version" guard can't drift between the two."""
    if workflow.active_version_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workflow has no published version")
    version = await WorkflowVersionRepository(session, org_id).get(workflow.active_version_id)
    if version is None or version.status != "published":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="workflow has no published version")
    return version


async def execute_workflow_run(
    session: AsyncSession,
    org_id: uuid.UUID,
    workflow: Workflow,
    version: WorkflowVersion,
    *,
    request: ManualRunRequest,
    actor_user_id: uuid.UUID | None,
    settings: Settings,
) -> ManualRunResult:
    """Run ``version`` for real and return the run summary.

    ``actor_user_id`` is the acting user's profile id for a browser session, or
    ``None`` for an API-key-driven run.
    """
    dispatcher = build_dispatch_service(session, settings)

    # A manual (BPMN "none" start) workflow runs with the caller-supplied input
    # variables the trigger declares; record fields are irrelevant and ignored.
    # Inputs are validated/coerced against the declared schema (undeclared keys
    # dropped, required ones enforced) so a caller can only supply the variables
    # the author designed.
    if is_manual_trigger(version.definition) or workflow.entity_definition_id is None:
        try:
            inputs = coerce_inputs(version.definition, request.inputs)
        except InputValidationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        run, executed = await dispatcher.run_version_manually(
            org_id,
            workflow,
            version,
            operation="manual",
            record_id=None,
            before=None,
            after=None,
            inputs=inputs,
            actor_user_id=actor_user_id,
        )
        return ManualRunResult(
            run_id=run.id,
            status=run.status,
            conditions_matched=bool(run.conditions_matched),
            actions_executed=executed,
            error=run.error,
        )

    # SECURITY: never trust client-supplied record data for an entity-bound run.
    #  * With a record_id, load before/after from the real entity table scoped to
    #    this org + the workflow's entity — a cross-org/cross-entity id can't
    #    resolve, and the client's before/after are ignored entirely.
    #  * Without a record_id, a side-effecting action still runs, but its record
    #    context is NULLED so only the workflow's own author-defined config can
    #    leave the org.
    if request.record_id is not None:
        record = await dispatcher.load_trigger_record(org_id, workflow.entity_definition_id, request.record_id)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="record not found for this workflow's entity",
            )
        before, after = record, record
    else:
        # Legacy `action` nodes and v2 `task` nodes both carry an action_type;
        # inspect both so a side-effecting step in either vocabulary is caught.
        action_types = {
            node.get("data", {}).get("action_type")
            for node in version.definition.get("nodes", [])
            if node.get("type") in ("action", "task")
        }
        if action_types & SIDE_EFFECTING_ACTIONS:
            before, after = None, None  # never trust client data outbound
        else:
            before, after = request.before, request.after

    run, executed = await dispatcher.run_version_manually(
        org_id,
        workflow,
        version,
        operation=request.operation,
        record_id=request.record_id,
        before=before,
        after=after,
        actor_user_id=actor_user_id,
    )
    return ManualRunResult(
        run_id=run.id,
        status=run.status,
        conditions_matched=bool(run.conditions_matched),
        actions_executed=executed,
        error=run.error,
    )
