"""Workflow dispatcher: claim outbox events, match workflows, run them.

Designed to run on a **privileged** session (like ``get_db``) so a single sweep
covers every tenant. Claims a bounded batch with ``FOR UPDATE SKIP LOCKED``
(safe across concurrent sweepers), then for each event evaluates the matching
workflows' graphs and executes their actions in-process, writing run + step rows
for the monitoring UI.

Loop guard: a mutation caused by an action carries ``origin_run_id`` on its
outbox row; the run it spawns inherits ``depth + 1``. Beyond ``MAX_DEPTH`` the
run is recorded as ``skipped`` rather than executed.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.custom_entity import EntityDefinition
from api.models.workflow import WorkflowRun
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import OutboxWriter, WorkflowRepository, WorkflowRunRepository
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError
from api.services.workflow.evaluator import evaluate_graph, trigger_matches

logger = logging.getLogger(__name__)

MAX_DEPTH = 8


def _as_dict(value: Any) -> dict[str, Any] | None:
    """The raw ``RETURNING o.*`` claim returns JSONB as an undecoded string
    (asyncpg has no type context there); normalise to a dict."""
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return value


def _changed_fields(before: dict[str, Any] | None, after: dict[str, Any] | None) -> set[str]:
    before = before or {}
    after = after or {}
    keys = set(before) | set(after)
    return {k for k in keys if before.get(k) != after.get(k)}


def _trigger_data(definition: dict[str, Any]) -> dict[str, Any]:
    for node in definition.get("nodes", []):
        if node.get("type") == "trigger":
            return node.get("data", {})
    return {}


class WorkflowDispatchService:
    def __init__(self, session: AsyncSession, *, webhook_allowlist: tuple[str, ...] = ()) -> None:
        self._session = session
        self._webhook_allowlist = webhook_allowlist

    async def process_pending(self, *, limit: int = 100, max_depth: int = MAX_DEPTH) -> dict[str, int]:
        """Claim and process a batch of pending outbox events. Returns counters."""
        claimed = (
            await self._session.execute(
                text(
                    """
                    UPDATE workflow_outbox o
                    SET status='claimed', claimed_at=now(), attempts=attempts+1
                    WHERE (o.id, o.created_at) IN (
                        SELECT id, created_at FROM workflow_outbox
                        WHERE status='pending'
                        ORDER BY seq
                        LIMIT :lim
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING o.*
                    """
                ),
                {"lim": limit},
            )
        ).mappings().all()

        counters = {"events": len(claimed), "runs": 0, "actions": 0, "skipped": 0}
        for event in claimed:
            ev = dict(event)
            keys = {"id": ev["id"], "ca": ev["created_at"]}
            try:
                # Per-event savepoint: an unexpected failure rolls back only this
                # event's partial writes, never the already-processed ones.
                async with self._session.begin_nested():
                    delta = await self._process_event(ev, max_depth=max_depth)
                    await self._session.execute(
                        text("UPDATE workflow_outbox SET status='done' WHERE id=:id AND created_at=:ca"), keys
                    )
                for k, v in delta.items():
                    counters[k] = counters.get(k, 0) + v
            except Exception:  # noqa: BLE001 - one poison event must not sink the batch
                logger.exception("workflow event %s failed; marking skipped", ev.get("id"))
                await self._session.execute(
                    text("UPDATE workflow_outbox SET status='skipped' WHERE id=:id AND created_at=:ca"), keys
                )
                counters["skipped"] = counters.get("skipped", 0) + 1
        return counters

    async def _process_event(self, event: dict[str, Any], *, max_depth: int) -> dict[str, int]:
        org_id: uuid.UUID = event["org_id"]
        operation: str = event["operation"]
        before = _as_dict(event["before_data"])
        after = _as_dict(event["after_data"])
        context = {"before": before, "after": after}
        changed = _changed_fields(before, after)
        depth = await self._depth_for(org_id, event["origin_run_id"])

        wf_repo = WorkflowRepository(self._session, org_id)
        run_repo = WorkflowRunRepository(self._session, org_id)
        matches = await wf_repo.list_enabled_for_entity(event["entity_definition_id"])

        delta = {"runs": 0, "actions": 0, "skipped": 0}
        for workflow, version in matches:
            if not trigger_matches(_trigger_data(version.definition), operation, changed):
                continue

            run = await run_repo.create_run_if_absent(
                workflow_id=workflow.id,
                workflow_version_id=version.id,
                outbox_id=event["id"],
                outbox_seq=event["seq"],
                created_at=event["created_at"],
                trigger_operation=operation,
                record_id=event["record_id"],
                input_snapshot={"before": before, "after": after},
                depth=depth,
            )
            if run is None:  # another sweeper already created it
                continue
            delta["runs"] += 1

            if depth > max_depth:
                await self._finish_run(run, status="skipped", error=f"max depth {max_depth} exceeded")
                delta["skipped"] += 1
                continue

            result = evaluate_graph(version.definition, context)
            if result.error is not None:
                await self._finish_run(run, status="failed", error=result.error, matched=False)
                continue
            if not result.matched:
                await self._finish_run(run, status="skipped", matched=False)
                delta["skipped"] += 1
                continue

            executed, ok = await self._run_actions(org_id, run, event, result.actions)
            delta["actions"] += executed
            await self._finish_run(run, status="succeeded" if ok else "failed", matched=True)
        return delta

    async def _run_actions(
        self, org_id: uuid.UUID, run: WorkflowRun, event: dict[str, Any], actions: list[dict[str, Any]]
    ) -> tuple[int, bool]:
        run_repo = WorkflowRunRepository(self._session, org_id)
        record_id = event["record_id"]
        executed = 0
        for index, node in enumerate(actions):
            data = node.get("data", {})
            action_type = data.get("action_type", "")
            step = await run_repo.add_step(
                run=run, node_id=node["id"], action_type=action_type, step_index=index
            )
            handler = ACTION_REGISTRY.get(action_type)
            ctx = ActionContext(
                org_id=org_id,
                record_id=record_id,
                before=_as_dict(event["before_data"]),
                after=_as_dict(event["after_data"]),
                config=data.get("config", {}),
                trigger_repo=lambda: self._repo_by_id(org_id, event["entity_definition_id"], run.id),
                repo_for_slug=lambda slug: self._repo_by_slug(org_id, slug, run.id),
                webhook_allowlist=self._webhook_allowlist,
            )
            try:
                if handler is None:
                    raise ActionError(f"unknown action type: {action_type!r}")
                output = await handler.execute(ctx)
                step.status = "succeeded"
                step.output = output
                step.finished_at = func.now()
                executed += 1
            except Exception as exc:  # noqa: BLE001 - failures are recorded, not raised
                step.status = "failed"
                step.error = str(exc)
                step.finished_at = func.now()
                await self._session.flush()
                if not data.get("continue_on_error", False):
                    return executed, False
            await self._session.flush()
        return executed, True

    async def _finish_run(
        self, run: WorkflowRun, *, status: str, error: str | None = None, matched: bool | None = None
    ) -> None:
        run.status = status
        run.finished_at = func.now()  # type: ignore[assignment]
        if error is not None:
            run.error = error
        if matched is not None:
            run.conditions_matched = matched
        await self._session.flush()

    async def _depth_for(self, org_id: uuid.UUID, origin_run_id: uuid.UUID | None) -> int:
        if origin_run_id is None:
            return 0
        result = await self._session.execute(
            select(WorkflowRun.depth).where(
                WorkflowRun.id == origin_run_id, WorkflowRun.org_id == org_id
            )
        )
        parent_depth = result.scalar_one_or_none()
        return (parent_depth or 0) + 1

    async def _repo_by_id(
        self, org_id: uuid.UUID, definition_id: uuid.UUID, origin_run_id: uuid.UUID
    ) -> DynamicEntityRepository:
        definition = await EntityDefinitionRepository(self._session, org_id).get(definition_id)
        if definition is None:
            raise ActionError("entity definition not found")
        return await self._build_repo(org_id, definition, origin_run_id)

    async def _repo_by_slug(
        self, org_id: uuid.UUID, slug: str, origin_run_id: uuid.UUID
    ) -> DynamicEntityRepository:
        definition = await EntityDefinitionRepository(self._session, org_id).get_by_slug(slug)
        if definition is None:
            raise ActionError(f"entity not found: {slug!r}")
        return await self._build_repo(org_id, definition, origin_run_id)

    async def _build_repo(
        self, org_id: uuid.UUID, definition: EntityDefinition, origin_run_id: uuid.UUID
    ) -> DynamicEntityRepository:
        fields = await EntityFieldRepository(self._session, org_id).list_for_definition(definition.id)
        rels = await EntityRelationshipRepository(self._session, org_id).list_for_source(definition.id)
        return DynamicEntityRepository(
            self._session,
            org_id,
            definition,
            fields,
            rels,
            outbox=OutboxWriter(self._session),
            origin_run_id=origin_run_id,
        )
