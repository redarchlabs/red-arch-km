"""Workflow authoring service: CRUD, versioning, publish/fork, dry-run test.

The dry-run test shares the same ``evaluate_graph`` the dispatcher uses, then
calls each action handler's ``simulate`` (never ``execute``) — so a test can
never cause a side effect.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.workflow import Workflow, WorkflowVersion
from api.repositories.custom_entity import EntityDefinitionRepository
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
)
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext
from api.services.workflow.evaluator import evaluate_graph


class WorkflowError(Exception):
    """Base workflow authoring error."""


class WorkflowNotFoundError(WorkflowError):
    """404."""


class WorkflowConflictError(WorkflowError):
    """409 (e.g. editing a published version in place)."""


async def _unsupported_repo(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - guard
    raise RuntimeError("record repositories are not available during dry-run")


class WorkflowService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._workflows = WorkflowRepository(session, org_id)
        self._versions = WorkflowVersionRepository(session, org_id)
        self._runs = WorkflowRunRepository(session, org_id)

    # --- workflows ---
    async def create_workflow(
        self, *, name: str, entity_definition_id: uuid.UUID, description: str | None
    ) -> Workflow:
        if await EntityDefinitionRepository(self._session, self._org_id).get(entity_definition_id) is None:
            raise WorkflowNotFoundError("entity not found")
        return await self._workflows.create(
            name=name, entity_definition_id=entity_definition_id, description=description
        )

    async def get_workflow(self, workflow_id: uuid.UUID) -> Workflow:
        wf = await self._workflows.get(workflow_id)
        if wf is None:
            raise WorkflowNotFoundError("workflow not found")
        return wf

    # --- versions ---
    async def save_draft(self, workflow_id: uuid.UUID, definition: dict[str, Any]) -> WorkflowVersion:
        """Create a new draft version (also used to fork a published one)."""
        await self.get_workflow(workflow_id)
        number = await self._versions.next_version_number(workflow_id)
        return await self._versions.create(
            workflow_id=workflow_id, version_number=number, definition=definition
        )

    async def publish(self, workflow_id: uuid.UUID, version_id: uuid.UUID) -> WorkflowVersion:
        wf = await self.get_workflow(workflow_id)
        version = await self._versions.get(version_id)
        if version is None or version.workflow_id != workflow_id:
            raise WorkflowNotFoundError("version not found")
        version.status = "published"
        version.published_at = func.now()  # type: ignore[assignment]
        # Archive the previously-active version and point the workflow at this one.
        if wf.active_version_id and wf.active_version_id != version_id:
            prior = await self._versions.get(wf.active_version_id)
            if prior is not None and prior.status == "published":
                prior.status = "archived"
        await self._workflows.update(wf, active_version_id=version_id)
        await self._session.flush()
        # published_at is a server-side func.now() expression, expired by the
        # flush; without an explicit refresh, serializing the row later
        # lazy-loads it OUTSIDE the async greenlet → MissingGreenlet → 500.
        await self._session.refresh(version, ["published_at"])
        return version

    # --- monitoring ---
    async def runs(self, workflow_id: uuid.UUID, *, limit: int = 50) -> list[Any]:
        await self.get_workflow(workflow_id)
        return await self._runs.list_for_workflow(workflow_id, limit=limit)

    async def run_steps(self, run_id: uuid.UUID) -> list[Any]:
        return await self._runs.steps_for_run(run_id)

    # --- dry-run test ---
    async def test_version(
        self, version_id: uuid.UUID, *, operation: str, before: dict[str, Any] | None, after: dict[str, Any] | None
    ) -> dict[str, Any]:
        version = await self._versions.get(version_id)
        if version is None:
            raise WorkflowNotFoundError("version not found")

        context = {"before": before, "after": after}
        result = evaluate_graph(version.definition, context)

        steps: list[dict[str, Any]] = []
        for node in result.actions:
            data = node.get("data", {})
            action_type = data.get("action_type", "")
            handler = ACTION_REGISTRY.get(action_type)
            ctx = ActionContext(
                org_id=self._org_id,
                record_id=None,
                before=before,
                after=after,
                config=data.get("config", {}),
                trigger_repo=_unsupported_repo,
                repo_for_slug=_unsupported_repo,
            )
            simulated = handler.simulate(ctx) if handler is not None else {"error": f"unknown action {action_type!r}"}
            steps.append({"node_id": node["id"], "action_type": action_type, "simulated_output": simulated})

        return {
            "conditions_matched": result.matched,
            "error": result.error,
            "condition_trace": [{"node_id": t.node_id, "result": t.result} for t in result.trace],
            "steps": steps,
        }
