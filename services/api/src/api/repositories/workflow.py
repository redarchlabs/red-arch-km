"""Workflow repositories.

``OutboxWriter`` appends a change-capture row in the *same transaction* as a
custom-entity record mutation (called by ``DynamicEntityRepository``), giving
at-least-once delivery to the dispatcher. Other workflow repos (definitions,
versions, runs) are added alongside the dispatcher.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.workflow import (
    Workflow,
    WorkflowConnection,
    WorkflowInboundEndpoint,
    WorkflowOutbox,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowRunToken,
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
        source: str = "record",
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
            source=source,
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
        run_permission: dict[str, Any] | None = None,
    ) -> Workflow:
        if name is not None:
            wf.name = name
        if description is not None:
            wf.description = description
        if enabled is not None:
            wf.enabled = enabled
        if active_version_id is not None:
            wf.active_version_id = active_version_id
        if run_permission is not None:
            wf.run_permission = run_permission
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
        parent_run_id: uuid.UUID | None = None,
        parent_token_id: uuid.UUID | None = None,
    ) -> WorkflowRun | None:
        """Insert one run per (event x workflow). Returns None if it already exists.

        ``created_at`` is pinned to the source outbox event's timestamp — it is
        part of the ``uq_wf_run_event`` unique key (the partition key must be),
        so reprocessing the same event deterministically conflicts instead of
        creating a duplicate run with a fresh now()-timestamp. ``parent_run_id`` /
        ``parent_token_id`` link a child (call-activity) run back to the call token.
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
                parent_run_id=parent_run_id,
                parent_token_id=parent_token_id,
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

    async def get_by_id(self, run_id: uuid.UUID) -> WorkflowRun | None:
        """Fetch a run by id alone (no partition key).

        The composite PK is ``(id, created_at)``, but callers that only hold a
        run id (the AI assistant's monitor/debug tools, which never see the
        partition key) need a by-id lookup. ``id`` is a UUID so this resolves at
        most one row; RLS + the explicit ``org_id`` keep it tenant-scoped.
        """
        result = await self._session.execute(
            select(WorkflowRun).where(
                WorkflowRun.id == run_id,
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
        token_id: uuid.UUID | None = None,
    ) -> WorkflowRunStep:
        step = WorkflowRunStep(
            org_id=self._org_id,
            run_id=run.id,
            run_created_at=run.created_at,
            node_id=node_id,
            action_type=action_type,
            step_index=step_index,
            token_id=token_id,
            status="pending",
            started_at=func.now(),
        )
        self._session.add(step)
        await self._session.flush()
        return step

    async def allocate_step_index(self, run: WorkflowRun) -> int:
        """Atomically bump the run's monotonic step counter and return the new
        value. Called under the per-run advisory lock so concurrent tokens get
        distinct, ordered ``step_index`` values (and the run-wide step budget is
        enforced against a single counter regardless of branch fan-out)."""
        result = await self._session.execute(
            text(
                "UPDATE workflow_runs SET step_seq = step_seq + 1 "
                "WHERE id = :id AND created_at = :ca AND org_id = :org RETURNING step_seq"
            ),
            {"id": run.id, "ca": run.created_at, "org": self._org_id},
        )
        value = int(result.scalar_one())
        # Keep the in-memory ORM object in step with the DB counter (the raw
        # UPDATE bypasses the identity map) so the engine's step-budget check
        # reads a live value across advances.
        run.step_seq = value
        return value

    async def set_variables(self, run: WorkflowRun, updates: dict[str, Any]) -> None:
        """Merge ``updates`` into the run's variables at the DB level (jsonb ||)
        so parallel tokens writing different keys don't clobber each other."""
        if not updates:
            return
        safe = json_safe(updates)
        await self._session.execute(
            text(
                "UPDATE workflow_runs SET variables = coalesce(variables, '{}'::jsonb) || cast(:patch AS jsonb) "
                "WHERE id = :id AND created_at = :ca AND org_id = :org"
            ),
            {"patch": json.dumps(safe), "id": run.id, "ca": run.created_at, "org": self._org_id},
        )
        # Sync the in-memory ORM object (the raw UPDATE bypasses the identity map,
        # and sessions run with expire_on_commit=False) so a later token advance
        # in the same session sees the new variables.
        run.variables = {**(run.variables or {}), **safe}


class WorkflowTokenRepository:
    """Durable control-flow tokens for the BPMN token engine.

    ``created_at`` is pinned to the owning run's ``created_at`` (the partition
    key), so a run's tokens co-locate in one partition and every join/settle
    query stays partition-local.
    """

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def create(
        self,
        *,
        run: WorkflowRun,
        node_id: str,
        arrived_from_node_id: str | None = None,
        arrived_via_handle: str | None = None,
        parent_token_id: uuid.UUID | None = None,
        created_by_node: str | None = None,
        depth: int = 0,
        status: str = "active",
    ) -> WorkflowRunToken:
        token = WorkflowRunToken(
            org_id=self._org_id,
            run_id=run.id,
            run_created_at=run.created_at,
            node_id=node_id,
            arrived_from_node_id=arrived_from_node_id,
            arrived_via_handle=arrived_via_handle,
            parent_token_id=parent_token_id,
            created_by_node=created_by_node,
            depth=depth,
            status=status,
            started_at=func.now(),
        )
        self._session.add(token)
        await self._session.flush()
        return token

    async def get(self, token_id: uuid.UUID, created_at: datetime) -> WorkflowRunToken | None:
        result = await self._session.execute(
            select(WorkflowRunToken).where(
                WorkflowRunToken.id == token_id,
                WorkflowRunToken.created_at == created_at,
                WorkflowRunToken.org_id == self._org_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_run(self, run_id: uuid.UUID) -> list[WorkflowRunToken]:
        result = await self._session.execute(
            select(WorkflowRunToken)
            .where(WorkflowRunToken.run_id == run_id, WorkflowRunToken.org_id == self._org_id)
            .order_by(WorkflowRunToken.seq)
        )
        return list(result.scalars().all())

    async def live_count(self, run_id: uuid.UUID) -> int:
        """Tokens that could still advance the run (active/running/waiting)."""
        result = await self._session.execute(
            select(func.count())
            .select_from(WorkflowRunToken)
            .where(
                WorkflowRunToken.run_id == run_id,
                WorkflowRunToken.org_id == self._org_id,
                WorkflowRunToken.status.in_(("active", "running", "waiting")),
            )
        )
        return int(result.scalar_one())

    async def token_count(self, run_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(WorkflowRunToken)
            .where(WorkflowRunToken.run_id == run_id, WorkflowRunToken.org_id == self._org_id)
        )
        return int(result.scalar_one())

    async def buffered_at(self, run_id: uuid.UUID, node_id: str, wait_kind: str = "join") -> list[WorkflowRunToken]:
        """Tokens parked (``waiting``) at a converging node — the join buffer."""
        result = await self._session.execute(
            select(WorkflowRunToken).where(
                WorkflowRunToken.run_id == run_id,
                WorkflowRunToken.org_id == self._org_id,
                WorkflowRunToken.node_id == node_id,
                WorkflowRunToken.status == "waiting",
                WorkflowRunToken.wait_kind == wait_kind,
            )
        )
        return list(result.scalars().all())

    async def kill_all(self, run_id: uuid.UUID) -> None:
        """Terminate every live token of a run (terminate end event)."""
        await self._session.execute(
            text(
                "UPDATE workflow_run_tokens SET status='dead', finished_at=now() "
                "WHERE run_id=:run AND org_id=:org AND status IN ('active','running','waiting')"
            ),
            {"run": run_id, "org": self._org_id},
        )

    async def reactivate_dead(self, run_id: uuid.UUID) -> int:
        """Return a run's ``dead`` tokens to ``active`` so the engine re-drives
        them — the durable primitive behind retrying a failed run.

        Each reactivated token re-enters the node it died on (its ``node_id`` is
        unchanged), which re-dispatches that step: a genuine retry, not a replay.
        Lease/timer/finish bookkeeping is cleared so the token looks freshly
        arrived. Returns the number of tokens reactivated (0 = nothing to retry).

        PRECONDITION (like the raw UPDATEs in ``allocate_step_index`` /
        ``set_variables``): this raw SQL bypasses the identity map, so a caller
        that has already loaded this run's ``WorkflowRunToken`` rows into the
        session would see stale ``status='dead'`` afterward. The only caller
        (``TokenEngine.retry_run``) loads no token rows before this, and
        ``drive_run`` re-``SELECT``s tokens fresh — so no resync is needed here.
        A future caller that pre-loads tokens must ``expire``/re-select them.
        """
        result = await self._session.execute(
            text(
                "UPDATE workflow_run_tokens "
                "SET status='active', lease_owner=NULL, leased_at=NULL, "
                "    resume_at=NULL, finished_at=NULL "
                "WHERE run_id=:run AND org_id=:org AND status='dead' "
                "RETURNING id"
            ),
            {"run": run_id, "org": self._org_id},
        )
        # RETURNING + len(rows) rather than .rowcount: the async Result type
        # doesn't expose rowcount, and this matches the RETURNING style used by
        # allocate_step_index.
        return len(result.fetchall())


class WorkflowConnectionRepository:
    """Org-scoped CRUD for connector credentials (RLS-enforced).

    Stores/returns the secret Fernet-encrypted; decryption is the caller's job at
    execute time (the repo never holds the encryption key), so a secret is only
    ever plaintext inside the connector handler.
    """

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, connection_id: uuid.UUID) -> WorkflowConnection | None:
        result = await self._session.execute(
            select(WorkflowConnection).where(
                WorkflowConnection.id == connection_id, WorkflowConnection.org_id == self._org_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> WorkflowConnection | None:
        result = await self._session.execute(
            select(WorkflowConnection).where(
                WorkflowConnection.name == name, WorkflowConnection.org_id == self._org_id
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[WorkflowConnection]:
        result = await self._session.execute(
            select(WorkflowConnection)
            .where(WorkflowConnection.org_id == self._org_id)
            .order_by(WorkflowConnection.name)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        kind: str = "http",
        base_url: str | None = None,
        auth_type: str = "none",
        secret_encrypted: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> WorkflowConnection:
        conn = WorkflowConnection(
            org_id=self._org_id,
            name=name,
            kind=kind,
            base_url=base_url,
            auth_type=auth_type,
            secret_encrypted=secret_encrypted,
            config=config or {},
        )
        self._session.add(conn)
        await self._session.flush()
        return conn

    async def update(
        self,
        conn: WorkflowConnection,
        *,
        name: str | None = None,
        base_url: str | None = None,
        auth_type: str | None = None,
        secret_encrypted: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> WorkflowConnection:
        if name is not None:
            conn.name = name
        if base_url is not None:
            conn.base_url = base_url
        if auth_type is not None:
            conn.auth_type = auth_type
        if secret_encrypted is not None:
            conn.secret_encrypted = secret_encrypted
        if config is not None:
            conn.config = config
        await self._session.flush()
        return conn

    async def delete(self, conn: WorkflowConnection) -> None:
        await self._session.delete(conn)
        await self._session.flush()


class WorkflowInboundEndpointRepository:
    """Public webhook endpoints that start a workflow run.

    Management (create/list/delete) is org-scoped + RLS-enforced. The public
    receiver resolves an endpoint by ``token_hash`` alone (the token is the
    secret) on a privileged session, so ``get_by_token_hash`` deliberately does
    not filter by org.
    """

    def __init__(self, session: AsyncSession, org_id: uuid.UUID | None = None) -> None:
        self._session = session
        self._org_id = org_id

    async def get_by_token_hash(self, token_hash: str) -> WorkflowInboundEndpoint | None:
        """Global lookup by the unique token hash (used by the public receiver)."""
        result = await self._session.execute(
            select(WorkflowInboundEndpoint).where(WorkflowInboundEndpoint.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[WorkflowInboundEndpoint]:
        result = await self._session.execute(
            select(WorkflowInboundEndpoint)
            .where(WorkflowInboundEndpoint.org_id == self._org_id)
            .order_by(WorkflowInboundEndpoint.name)
        )
        return list(result.scalars().all())

    async def get(self, endpoint_id: uuid.UUID) -> WorkflowInboundEndpoint | None:
        result = await self._session.execute(
            select(WorkflowInboundEndpoint).where(
                WorkflowInboundEndpoint.id == endpoint_id, WorkflowInboundEndpoint.org_id == self._org_id
            )
        )
        return result.scalar_one_or_none()

    async def create(self, *, name: str, workflow_id: uuid.UUID, token_hash: str) -> WorkflowInboundEndpoint:
        endpoint = WorkflowInboundEndpoint(
            org_id=self._org_id, name=name, workflow_id=workflow_id, token_hash=token_hash, enabled=True
        )
        self._session.add(endpoint)
        await self._session.flush()
        return endpoint

    async def delete(self, endpoint: WorkflowInboundEndpoint) -> None:
        await self._session.delete(endpoint)
        await self._session.flush()
