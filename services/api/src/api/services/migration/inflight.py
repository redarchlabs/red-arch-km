"""In-flight workflow-run guard for destructive promotions.

Republishing a workflow is safe: a run pins its ``workflow_version_id`` and the
engine loads that immutable version by id regardless of status, so an in-flight
run finishes on the snapshot it started with. What is NOT safe is *deleting* an
object a live run depends on — deleting a workflow removes the pinned version
(run hard-fails "version missing"); deleting an entity a running workflow targets
breaks it.

This guard finds non-terminal runs (``pending`` / ``running`` / ``waiting``) that
depend on an object slated for deletion, so the promotion can BLOCK until they
drain — unless the operator explicitly overrides. It is only consulted for the
destructive path; additive/republish promotions never call it.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.workflow import WorkflowRun
from api.repositories.custom_entity import EntityDefinitionRepository
from api.repositories.workflow import WorkflowRepository

# Runs in these states can still advance; a destructive change under them is unsafe.
NON_TERMINAL_RUN_STATUSES = ("pending", "running", "waiting")


class InFlightBlocker(BaseModel):
    """A live-run dependency that blocks deleting one object."""

    resource_type: str
    lineage_id: str
    name: str
    run_count: int
    reason: str


class InFlightGuard:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def check(self, deletions: dict[str, set[str]]) -> list[InFlightBlocker]:
        """Return a blocker per to-be-deleted object that has non-terminal runs.

        Covers the two unambiguous hazards: deleting a workflow with live runs, and
        deleting an entity that a workflow with live runs targets. An empty list
        means the destructive plan is safe to apply now.
        """
        blockers: list[InFlightBlocker] = []
        blockers.extend(await self._check_workflows(deletions.get("workflows") or set()))
        blockers.extend(await self._check_entities(deletions.get("entities") or set()))
        return blockers

    async def _check_workflows(self, lineages: set[str]) -> list[InFlightBlocker]:
        if not lineages:
            return []
        workflows = await WorkflowRepository(self._session, self._org_id).list_all()
        out: list[InFlightBlocker] = []
        for wf in workflows:
            if self._lineage(wf) not in lineages:
                continue
            count = await self._run_count([wf.id])
            if count:
                out.append(
                    InFlightBlocker(
                        resource_type="workflows",
                        lineage_id=self._lineage(wf),
                        name=wf.name,
                        run_count=count,
                        reason=f"{count} in-flight run(s) are pinned to this workflow and would hard-fail",
                    )
                )
        return out

    async def _check_entities(self, lineages: set[str]) -> list[InFlightBlocker]:
        if not lineages:
            return []
        defs, _ = await EntityDefinitionRepository(self._session, self._org_id).list_all(limit=1000)
        target_ids = {d.id for d in defs if self._lineage(d) in lineages}
        if not target_ids:
            return []
        workflows = await WorkflowRepository(self._session, self._org_id).list_all()
        out: list[InFlightBlocker] = []
        for d in defs:
            if self._lineage(d) not in lineages:
                continue
            wf_ids = [wf.id for wf in workflows if wf.entity_definition_id == d.id]
            if not wf_ids:
                continue
            count = await self._run_count(wf_ids)
            if count:
                out.append(
                    InFlightBlocker(
                        resource_type="entities",
                        lineage_id=self._lineage(d),
                        name=d.name,
                        run_count=count,
                        reason=f"{count} in-flight run(s) belong to workflows on this entity",
                    )
                )
        return out

    async def _run_count(self, workflow_ids: list[uuid.UUID]) -> int:
        if not workflow_ids:
            return 0
        rows = (
            await self._session.execute(
                select(WorkflowRun.id).where(
                    WorkflowRun.org_id == self._org_id,
                    WorkflowRun.workflow_id.in_(workflow_ids),
                    WorkflowRun.status.in_(NON_TERMINAL_RUN_STATUSES),
                )
            )
        ).all()
        return len(rows)

    @staticmethod
    def _lineage(row: Any) -> str:
        return str(getattr(row, "lineage_id", None) or row.id)
