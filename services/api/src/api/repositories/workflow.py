"""Workflow repositories.

``OutboxWriter`` appends a change-capture row in the *same transaction* as a
custom-entity record mutation (called by ``DynamicEntityRepository``), giving
at-least-once delivery to the dispatcher. Other workflow repos (definitions,
versions, runs) are added alongside the dispatcher.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.workflow import (
    Workflow,
    WorkflowOutbox,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowVersion,
)


def json_safe(value: Any) -> Any:
    """Recursively convert a record dict to JSON-serialisable primitives
    (uuid -> str, datetime/date -> isoformat) for JSONB storage."""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class OutboxWriter:
    """Writes ``workflow_outbox`` rows on the caller's tenant session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def write(
        self,
        *,
        org_id: uuid.UUID,
        entity_definition_id: uuid.UUID,
        entity_table: str,
        operation: str,
        record_id: uuid.UUID,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        actor_user_id: uuid.UUID | None = None,
        origin_run_id: uuid.UUID | None = None,
    ) -> None:
        row = WorkflowOutbox(
            org_id=org_id,
            entity_definition_id=entity_definition_id,
            entity_table=entity_table,
            record_id=record_id,
            operation=operation,
            before_data=json_safe(before) if before is not None else None,
            after_data=json_safe(after) if after is not None else None,
            actor_user_id=actor_user_id,
            origin_run_id=origin_run_id,
        )
        self._session.add(row)
        await self._session.flush()


class WorkflowRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, workflow_id: uuid.UUID) -> Workflow | None:
        result = await self._session.execute(
            select(Workflow).where(Workflow.id == workflow_id, Workflow.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[Workflow]:
        result = await self._session.execute(
            select(Workflow).where(Workflow.org_id == self._org_id).order_by(Workflow.name)
        )
        return list(result.scalars().all())

    async def list_enabled_for_entity(
        self, entity_definition_id: uuid.UUID
    ) -> list[tuple[Workflow, WorkflowVersion]]:
        """Enabled workflows for an entity paired with their published active version."""
        stmt = (
            select(Workflow, WorkflowVersion)
            .join(WorkflowVersion, WorkflowVersion.id == Workflow.active_version_id)
            .where(
                Workflow.org_id == self._org_id,
                Workflow.entity_definition_id == entity_definition_id,
                Workflow.enabled.is_(True),
                WorkflowVersion.status == "published",
            )
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def create(self, *, name: str, entity_definition_id: uuid.UUID, description: str | None) -> Workflow:
        wf = Workflow(
            name=name,
            entity_definition_id=entity_definition_id,
            description=description,
            org_id=self._org_id,
        )
        self._session.add(wf)
        await self._session.flush()
        return wf

    async def update(
        self,
        wf: Workflow,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        active_version_id: uuid.UUID | None = None,
    ) -> Workflow:
        if name is not None:
            wf.name = name
        if description is not None:
            wf.description = description
        if enabled is not None:
            wf.enabled = enabled
        if active_version_id is not None:
            wf.active_version_id = active_version_id
        await self._session.flush()
        return wf

    async def delete(self, wf: Workflow) -> None:
        await self._session.delete(wf)
        await self._session.flush()


class WorkflowVersionRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, version_id: uuid.UUID) -> WorkflowVersion | None:
        result = await self._session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.id == version_id, WorkflowVersion.org_id == self._org_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: uuid.UUID) -> list[WorkflowVersion]:
        result = await self._session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.org_id == self._org_id)
            .order_by(WorkflowVersion.version_number.desc())
        )
        return list(result.scalars().all())

    async def next_version_number(self, workflow_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(WorkflowVersion.version_number), 0)).where(
                WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.org_id == self._org_id
            )
        )
        return int(result.scalar_one()) + 1

    async def create(
        self,
        *,
        workflow_id: uuid.UUID,
        version_number: int,
        definition: dict[str, Any],
        created_by_id: uuid.UUID | None = None,
    ) -> WorkflowVersion:
        version = WorkflowVersion(
            workflow_id=workflow_id,
            version_number=version_number,
            definition=definition,
            status="draft",
            created_by_id=created_by_id,
            org_id=self._org_id,
        )
        self._session.add(version)
        await self._session.flush()
        return version


class WorkflowRunRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def create_run_if_absent(
        self,
        *,
        workflow_id: uuid.UUID,
        workflow_version_id: uuid.UUID,
        outbox_id: uuid.UUID,
        outbox_seq: int | None,
        created_at: datetime,
        trigger_operation: str,
        record_id: uuid.UUID | None,
        input_snapshot: dict[str, Any] | None,
        depth: int,
    ) -> WorkflowRun | None:
        """Insert one run per (event x workflow). Returns None if it already exists.

        ``created_at`` is pinned to the source outbox event's timestamp — it is
        part of the ``uq_wf_run_event`` unique key (the partition key must be),
        so reprocessing the same event deterministically conflicts instead of
        creating a duplicate run with a fresh now()-timestamp.
        """
        stmt = (
            pg_insert(WorkflowRun.__table__)
            .values(
                id=uuid.uuid4(),
                created_at=created_at,
                org_id=self._org_id,
                workflow_id=workflow_id,
                workflow_version_id=workflow_version_id,
                outbox_id=outbox_id,
                outbox_seq=outbox_seq,
                trigger_operation=trigger_operation,
                record_id=record_id,
                status="running",
                input_snapshot=input_snapshot,
                depth=depth,
                started_at=func.now(),
            )
            .on_conflict_do_nothing(constraint="uq_wf_run_event")
            .returning(WorkflowRun.__table__)
        )
        row = (await self._session.execute(stmt)).mappings().one_or_none()
        if row is None:
            return None
        return await self.get(row["id"], row["created_at"])

    async def get(self, run_id: uuid.UUID, created_at: datetime) -> WorkflowRun | None:
        result = await self._session.execute(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id,
                WorkflowRun.created_at == created_at,
                WorkflowRun.org_id == self._org_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: uuid.UUID, *, limit: int = 50) -> list[WorkflowRun]:
        result = await self._session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_id == workflow_id, WorkflowRun.org_id == self._org_id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def steps_for_run(self, run_id: uuid.UUID) -> list[WorkflowRunStep]:
        result = await self._session.execute(
            select(WorkflowRunStep)
            .where(WorkflowRunStep.run_id == run_id, WorkflowRunStep.org_id == self._org_id)
            .order_by(WorkflowRunStep.step_index)
        )
        return list(result.scalars().all())

    async def add_step(
        self,
        *,
        run: WorkflowRun,
        node_id: str,
        action_type: str,
        step_index: int,
    ) -> WorkflowRunStep:
        step = WorkflowRunStep(
            org_id=self._org_id,
            run_id=run.id,
            run_created_at=run.created_at,
            node_id=node_id,
            action_type=action_type,
            step_index=step_index,
            status="pending",
            started_at=func.now(),
        )
        self._session.add(step)
        await self._session.flush()
        return step
