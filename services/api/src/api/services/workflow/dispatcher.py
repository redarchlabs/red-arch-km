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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.custom_entity import EntityDefinition
from api.models.workflow import Workflow, WorkflowRun, WorkflowVersion
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import (
    OutboxWriter,
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowVersionRepository,
    json_safe,
)
from api.schemas.workflow_definition import WorkflowDefinitionModel
from api.services.workflow.actions import ACTION_REGISTRY, ActionContext, ActionError
from api.services.workflow.engine import TokenEngine
from api.services.workflow.evaluator import evaluate_graph, trigger_matches
from api.services.workflow.schedule import is_schedule_due

logger = logging.getLogger(__name__)

MAX_DEPTH = 8
# Hard cap on the total steps a single run may execute across ALL its resume
# cycles. MAX_DEPTH only bounds outbox-chained runs; it does NOT bound a run that
# loops through a delay node back onto itself (a1 -> delay -> a2 -> a1), because
# each resume re-enters the graph with a fresh ``visited`` set. This cap makes
# such a user-authored cycle terminate (as failed) instead of resuspending
# forever.
MAX_RUN_STEPS = 200


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


def _schedule_of(definition: dict[str, Any]) -> dict[str, Any] | None:
    """The trigger's ``schedule`` block, if any (drives time-based firing)."""
    schedule = _trigger_data(definition).get("schedule")
    return schedule if isinstance(schedule, dict) else None


class WorkflowDispatchService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        webhook_allowlist: tuple[str, ...] = (),
        public_base_url: str = "",
        email_sender: Any = None,
        org_encryption_key: str = "",
        token_engine_enabled: bool = True,
        trusted_local_hosts: tuple[str, ...] = (),
        settings: Any = None,
    ) -> None:
        self._session = session
        self._webhook_allowlist = webhook_allowlist
        self._trusted_local_hosts = trusted_local_hosts
        self._public_base_url = public_base_url
        self._email_sender = email_sender
        self._org_encryption_key = org_encryption_key
        self._token_engine_enabled = token_engine_enabled
        # App Settings — threaded to the token engine + legacy actions so the
        # knowledge_search action can reach brain-api. None ⇒ search unavailable.
        self._settings = settings

    # ---- dual-engine selection ------------------------------------------ #
    def _use_token_engine(self, definition: dict[str, Any]) -> bool:
        """v2 graphs (schema_version >= 2 or any new node type) run on the token
        engine; legacy v1 graphs stay on the walker. The flag is a kill-switch."""
        if not self._token_engine_enabled:
            return False
        try:
            return WorkflowDefinitionModel.parse(definition).is_v2
        except Exception:  # noqa: BLE001 - a malformed v2 def still shouldn't crash the sweep
            return False

    def _token_engine(self) -> TokenEngine:
        return TokenEngine(
            self._session,
            webhook_allowlist=self._webhook_allowlist,
            trusted_local_hosts=self._trusted_local_hosts,
            public_base_url=self._public_base_url,
            email_sender=self._email_sender,
            org_encryption_key=self._org_encryption_key,
            settings=self._settings,
        )

    async def _run_token_engine(self, run: WorkflowRun, definition: dict[str, Any]) -> int:
        """Seed + drive a v2 run to quiescence within the caller's tenant scope;
        returns the number of task steps that ran (for the dispatch counters)."""
        engine = self._token_engine()
        await engine.start_run(run, definition)
        await engine.drive_run(run)
        steps = await WorkflowRunRepository(self._session, run.org_id).steps_for_run(run.id)
        return sum(1 for step in steps if step.status == "succeeded")

    # ---- connection resolution (mirrors WorkflowRunner) ------------------ #
    async def _search_knowledge(self, org_id: uuid.UUID, opts: dict[str, Any]) -> dict[str, Any]:
        """Org-scoped hybrid RAG lookup for the knowledge_search action (legacy
        walker path). Mirrors ``ActionExecutor._search_knowledge`` — unrestricted
        within the org, deferred brain-api import. ``opts`` = ``{query, use_knowledge_graph}``."""
        from api.services.workflow.actions import ActionError

        if self._settings is None:
            raise ActionError("knowledge search requires Settings (not wired in this context)")
        from api.services.brain_client import BrainAPIClient

        client = BrainAPIClient(self._settings)
        return await client.vector_chat(
            tenant_id=str(org_id),
            query=str(opts.get("query", "")),
            access_keys=None,
            use_knowledge_graph=bool(opts.get("use_knowledge_graph", True)),
        )

    async def _retrieve_knowledge(self, org_id: uuid.UUID, query: str) -> dict[str, Any]:
        """Retrieval-only KB lookup for knowledge_search(synthesize=False) (legacy
        walker path). Mirrors ``ActionExecutor._retrieve_knowledge``."""
        from api.services.workflow.actions import ActionError
        from api.services.workflow.runner import _format_passages, _passage_sources

        if self._settings is None:
            raise ActionError("knowledge retrieval requires Settings (not wired in this context)")
        from api.services.brain_client import BrainAPIClient

        client = BrainAPIClient(self._settings)
        result = await client.vector_search(tenant_id=str(org_id), query=query, access_keys=None)
        hits = result.get("hits", [])
        return {"answer": _format_passages(hits), "sources": _passage_sources(hits), "passages": hits}

    async def _summarize(self, org_id: uuid.UUID, opts: dict[str, Any]) -> str:
        """Small-LLM condensation for the summarize action (legacy walker path).
        Mirrors ``ActionExecutor._summarize`` — org OpenAI key then central."""
        from api.services.workflow.actions import ActionError

        if self._settings is None:
            raise ActionError("summarization requires Settings (not wired in this context)")
        key = await self._org_openai_key(org_id) or self._settings.openai_api_key.get_secret_value()
        if not key:
            raise ActionError("summarization requires an OpenAI API key (org or central)")
        from openai import AsyncOpenAI

        from api.services.spoken_summary import summarize_for_speech

        client = AsyncOpenAI(api_key=key)
        model = opts.get("model") or self._settings.openai_summary_model
        return await summarize_for_speech(
            client,
            model,
            text=str(opts.get("text") or ""),
            question=opts.get("question"),
            max_words=int(opts.get("max_words") or 30),
            instruction=opts.get("instruction"),
        )

    async def _org_openai_key(self, org_id: uuid.UUID) -> str | None:
        from api.models.org import Org
        from api.services.crypto import decrypt_secret

        org = await self._session.get(Org, org_id)
        stored = org.openai_api_key if org else None
        if not stored:
            return None
        return decrypt_secret(stored, self._settings.org_encryption_key.get_secret_value())

    async def _resolve_connection(self, org_id: uuid.UUID, name: str) -> Any:
        """Load a named connection (org-scoped) and decrypt its secret. Returns a
        ResolvedConnection or None. Mirrors ``WorkflowRunner._resolve_connection``
        so the legacy walker path (used by manual runs of schema_version 1 graphs)
        resolves named connections exactly like the token engine does. Without this
        an ``http_request``/``send_webhook`` with a ``connection`` fails with
        "connections are not available in this context" on the manual/legacy path."""
        from api.repositories.workflow import WorkflowConnectionRepository
        from api.services.crypto import decrypt_secret
        from api.services.workflow.actions import ResolvedConnection

        if not self._org_encryption_key:
            return None
        conn = await WorkflowConnectionRepository(self._session, org_id).get_by_name(name)
        if conn is None:
            return None
        secret = decrypt_secret(conn.secret_encrypted, self._org_encryption_key) if conn.secret_encrypted else None
        return ResolvedConnection(
            name=conn.name,
            base_url=conn.base_url,
            auth_type=conn.auth_type,
            secret=secret,
            config=conn.config or {},
        )

    # ---- per-tenant RLS downgrade ---------------------------------------- #
    async def _enter_tenant(self, org_id: uuid.UUID) -> None:
        """Downgrade the (privileged) sweep session to ``app_user`` + set the
        tenant GUC so RLS actually enforces for this unit of work.

        The batch is CLAIMED on the privileged connection role (cross-org, needs
        BYPASSRLS to see every tenant's rows). Once a unit is claimed, all of its
        follow-on reads/writes — including the dynamic-entity table writes an
        action performs — run downgraded, so PostgreSQL RLS is a real backstop:
        an action can never touch another org's data even if application-level
        scoping had a bug. ``SET LOCAL`` is transaction-scoped; it is rolled back
        automatically if the enclosing savepoint rolls back, and we RESET it
        explicitly (``_exit_tenant``) after a successful unit."""
        await self._session.execute(text("SET LOCAL ROLE app_user"))
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(org_id)},
        )

    async def _exit_tenant(self) -> None:
        """Restore the privileged connection role so the next unit's cross-org
        claim/bookkeeping runs unscoped again.

        We deliberately leave the tenant GUC as-is: cross-org work between units
        runs on the privileged (BYPASSRLS) role where the GUC is irrelevant, and
        ``_enter_tenant`` always re-sets the GUC to the correct org for the next
        unit — so a stale value can never widen a downgraded unit's visibility."""
        await self._session.execute(text("RESET ROLE"))

    async def process_pending(self, *, limit: int = 100, max_depth: int = MAX_DEPTH) -> dict[str, int]:
        """Claim and process a batch of pending outbox events. Returns counters."""
        claimed = (
            (
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
            )
            .mappings()
            .all()
        )

        counters = {"events": len(claimed), "runs": 0, "actions": 0, "skipped": 0}
        for event in claimed:
            ev = dict(event)
            keys = {"id": ev["id"], "ca": ev["created_at"]}
            try:
                # Per-event savepoint: an unexpected failure rolls back only this
                # event's partial writes, never the already-processed ones. All
                # work inside runs downgraded to the event's tenant (RLS backstop).
                async with self._session.begin_nested():
                    await self._enter_tenant(ev["org_id"])
                    delta = await self._process_event(ev, max_depth=max_depth)
                    await self._session.execute(
                        text("UPDATE workflow_outbox SET status='done' WHERE id=:id AND created_at=:ca"), keys
                    )
                await self._exit_tenant()
                for k, v in delta.items():
                    counters[k] = counters.get(k, 0) + v
            except Exception:  # noqa: BLE001 - one poison event must not sink the batch
                # The savepoint rolled back (reverting the SET LOCAL ROLE too), so
                # this UPDATE runs on the privileged role again. Be defensive and
                # RESET regardless in case the failure happened post-commit.
                await self._exit_tenant()
                logger.exception("workflow event %s failed; marking skipped", ev.get("id"))
                await self._session.execute(
                    text("UPDATE workflow_outbox SET status='skipped' WHERE id=:id AND created_at=:ca"), keys
                )
                counters["skipped"] = counters.get("skipped", 0) + 1
        return counters

    async def _process_event(
        self, event: dict[str, Any], *, max_depth: int, only_inline: bool = False
    ) -> dict[str, int]:
        org_id: uuid.UUID = event["org_id"]
        operation: str = event["operation"]
        before = _as_dict(event["before_data"])
        after = _as_dict(event["after_data"])
        context = {"before": before, "after": after}
        changed = _changed_fields(before, after)
        depth = await self._depth_for(org_id, event["origin_run_id"])

        source = event.get("source") or "record"
        wf_repo = WorkflowRepository(self._session, org_id)
        run_repo = WorkflowRunRepository(self._session, org_id)
        matches = await wf_repo.list_enabled_for_entity(event["entity_definition_id"])

        delta = {"runs": 0, "actions": 0, "skipped": 0}
        for workflow, version in matches:
            # Inline pass (in-request, right after the write): only the workflows
            # that opted into run_inline_on_change. The later beat sweep runs the
            # full set — the inline runs dedup out (same workflow x outbox event)
            # and the non-inline ones fire then.
            if only_inline and not workflow.run_inline_on_change:
                continue
            if not trigger_matches(_trigger_data(version.definition), operation, changed, source=source):
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

            if self._use_token_engine(version.definition):
                delta["actions"] += await self._run_token_engine(run, version.definition)
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
            await self._settle_run(run, result, ok)
        return delta

    async def run_inline_for_change(self, event: dict[str, Any], *, max_depth: int = MAX_DEPTH) -> dict[str, int]:
        """Run ONLY the ``run_inline_on_change`` workflows for a just-written record
        change, synchronously, from within the mutating request.

        ``event`` is the real ``workflow_outbox`` row (id/seq/created_at/before/after/
        …) so each inline run is keyed to that outbox event — when the beat sweep
        later processes the same row it dedups these runs and fires only the
        non-inline workflows. Call this AFTER the record write has flushed its
        outbox row; the caller should isolate it in a savepoint so a workflow/robot
        failure cannot roll back the record write."""
        return await self._process_event(event, max_depth=max_depth, only_inline=True)

    async def _settle_run(self, run: WorkflowRun, result: Any, ok: bool) -> None:
        """After running a path's actions, either fail, suspend at a delay, or succeed."""
        if not ok:
            await self._finish_run(run, status="failed", matched=True)
        elif result.paused and result.resume_node_id is not None:
            await self._suspend_run(run, result.resume_node_id, result.delay_seconds)
        else:
            # A delay with no successor node is equivalent to a completed run.
            await self._finish_run(run, status="succeeded", matched=True)

    async def _suspend_run(self, run: WorkflowRun, resume_node_id: str, delay_seconds: int) -> None:
        """Park a run at a delay node until ``resume_at`` (swept by run-timers)."""
        run.status = "waiting"
        run.conditions_matched = True
        run.resume_node_id = resume_node_id
        run.resume_at = datetime.now(UTC) + timedelta(seconds=max(0, delay_seconds))  # type: ignore[assignment]
        await self._session.flush()

    async def run_version_manually(
        self,
        org_id: uuid.UUID,
        workflow: Workflow,
        version: WorkflowVersion,
        *,
        operation: str,
        record_id: uuid.UUID | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        inputs: dict[str, Any] | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> tuple[WorkflowRun, int]:
        """Execute a workflow version FOR REAL against provided inputs (manual run).

        Reuses the same action engine as the outbox path — real side effects, a
        real ``workflow_run`` + step rows — but bypasses trigger matching (the
        operator chose to run it) while still honouring the graph's conditions.
        ``inputs`` are caller-supplied variables for a manual (on-demand) workflow,
        addressable as ``inputs.<key>`` in conditions/gateways and ``{{ inputs.<key> }}``
        in templated action fields. Returns ``(run, actions_executed)``.
        """
        inputs = inputs or {}
        run_repo = WorkflowRunRepository(self._session, org_id)
        synthetic_outbox_id = uuid.uuid4()
        run = await run_repo.create_run_if_absent(
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            outbox_id=synthetic_outbox_id,
            outbox_seq=None,
            created_at=datetime.now(UTC),
            trigger_operation=operation,
            record_id=record_id,
            input_snapshot={"before": before, "after": after, "inputs": inputs, "manual": True},
            depth=0,
        )
        if run is None:  # extraordinarily unlikely (fresh uuid) — treat as a conflict
            raise ActionError("could not create manual run")

        if self._use_token_engine(version.definition):
            executed = await self._run_token_engine(run, version.definition)
            return run, executed

        result = evaluate_graph(version.definition, {"before": before, "after": after, "inputs": inputs})
        if result.error is not None:
            await self._finish_run(run, status="failed", error=result.error, matched=False)
            return run, 0
        if not result.matched:
            await self._finish_run(run, status="skipped", matched=False)
            return run, 0

        event = {
            "id": synthetic_outbox_id,
            "seq": None,
            "record_id": record_id,
            "before_data": before,
            "after_data": after,
            "inputs": inputs,
            "entity_definition_id": workflow.entity_definition_id,
            "entity_table": "",
        }
        executed, ok = await self._run_actions(org_id, run, event, result.actions)
        await self._settle_run(run, result, ok)
        return run, executed

    async def load_trigger_record(
        self, org_id: uuid.UUID, definition_id: uuid.UUID, record_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Load a record for a manual run, scoped to ``org_id`` + ``definition_id``.

        The repository filters by org and is built from ``definition_id``, so a
        ``record_id`` that belongs to another org or another entity simply
        resolves to ``None`` (the caller turns that into a 404) — a client can
        never smuggle cross-org/cross-entity data into a manual run.
        """
        definition = await EntityDefinitionRepository(self._session, org_id).get(definition_id)
        if definition is None:
            return None
        fields = await EntityFieldRepository(self._session, org_id).list_for_definition(definition.id)
        rels = await EntityRelationshipRepository(self._session, org_id).list_for_source(definition.id)
        repo = DynamicEntityRepository(self._session, org_id, definition, fields, rels)
        record = await repo.get(record_id)
        return json_safe(record) if record is not None else None

    async def _run_actions(
        self,
        org_id: uuid.UUID,
        run: WorkflowRun,
        event: dict[str, Any],
        actions: list[dict[str, Any]],
        *,
        step_offset: int = 0,
    ) -> tuple[int, bool]:
        run_repo = WorkflowRunRepository(self._session, org_id)
        record_id = event["record_id"]
        executed = 0

        # Memoize record repos per (kind, key) for this invocation so N actions
        # against the same entity don't rebuild the repo (definition + fields +
        # relationships load) N times.
        repo_cache: dict[Any, DynamicEntityRepository] = {}

        async def _trigger_repo() -> DynamicEntityRepository:
            key = ("id", event["entity_definition_id"])
            if key not in repo_cache:
                repo_cache[key] = await self._repo_by_id(org_id, event["entity_definition_id"], run.id)
            return repo_cache[key]

        async def _repo_for_slug(slug: str) -> DynamicEntityRepository:
            key = ("slug", slug)
            if key not in repo_cache:
                repo_cache[key] = await self._repo_by_slug(org_id, slug, run.id)
            return repo_cache[key]

        for index, node in enumerate(actions):
            data = node.get("data", {})
            action_type = data.get("action_type", "")
            step = await run_repo.add_step(
                run=run, node_id=node["id"], action_type=action_type, step_index=step_offset + index
            )
            handler = ACTION_REGISTRY.get(action_type)
            ctx = ActionContext(
                org_id=org_id,
                record_id=record_id,
                before=_as_dict(event["before_data"]),
                after=_as_dict(event["after_data"]),
                inputs=event.get("inputs") or {},
                vars=(run.variables or {}),
                config=data.get("config", {}),
                trigger_repo=_trigger_repo,
                repo_for_slug=_repo_for_slug,
                webhook_allowlist=self._webhook_allowlist,
                trusted_local_hosts=self._trusted_local_hosts,
                mint_form_link=lambda form_id, rid, email: self._mint_form_link(org_id, form_id, rid, email),
                send_email=self._send_email,
                resolve_connection=lambda name: self._resolve_connection(org_id, name),
                search_knowledge=lambda opts: self._search_knowledge(org_id, opts),
                retrieve_knowledge=lambda query: self._retrieve_knowledge(org_id, query),
                summarize=lambda opts: self._summarize(org_id, opts),
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
                # Record the failure on the step, but also LOG it with context —
                # storing str(exc) to the DB alone loses the traceback an operator
                # needs to diagnose a broken action.
                logger.exception(
                    "workflow action failed (run=%s node=%s type=%s): %s",
                    run.id,
                    node.get("id"),
                    action_type,
                    exc,
                )
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
            select(WorkflowRun.depth).where(WorkflowRun.id == origin_run_id, WorkflowRun.org_id == org_id)
        )
        parent_depth = result.scalar_one_or_none()
        return (parent_depth or 0) + 1

    # ---- time-based work (swept together by the run-timers job) ---------- #
    async def process_timers(self, *, resume_limit: int = 50, schedule_limit: int = 100) -> dict[str, int]:
        """Resume due delayed runs + fire due scheduled workflows (one sweep)."""
        counters = await self.resume_waiting_runs(limit=resume_limit)
        for k, v in (await self.run_due_schedules(limit=schedule_limit)).items():
            counters[k] = counters.get(k, 0) + v
        return counters

    async def resume_waiting_runs(self, *, limit: int = 50) -> dict[str, int]:
        """Resume runs parked at a delay whose wait has elapsed.

        Runs on the privileged session (cross-org). Overlapping beat sweeps (with
        ``--concurrency>1``) would otherwise each SELECT the same due rows and
        double-execute a resumed run's actions. So we CLAIM atomically the same
        way ``process_pending`` claims outbox rows: flip ``waiting`` -> ``running``
        under ``FOR UPDATE SKIP LOCKED`` and only process what we claimed. A
        concurrent sweep skips the locked rows and, once we commit, sees them as
        ``running`` (no longer ``waiting``) — exactly-once resume."""
        now = datetime.now(UTC)
        claimed = (
            (
                await self._session.execute(
                    text(
                        """
                    UPDATE workflow_runs r
                    SET status='running', started_at=now()
                    WHERE (r.id, r.created_at) IN (
                        SELECT id, created_at FROM workflow_runs
                        WHERE status='waiting' AND resume_at <= :now
                        ORDER BY resume_at
                        LIMIT :lim
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING r.id, r.created_at, r.org_id
                    """
                    ),
                    {"now": now, "lim": limit},
                )
            )
            .mappings()
            .all()
        )

        counters = {"resumed": 0, "actions": 0}
        for row in claimed:
            org_id = row["org_id"]
            try:
                async with self._session.begin_nested():
                    await self._enter_tenant(org_id)
                    run = await WorkflowRunRepository(self._session, org_id).get(row["id"], row["created_at"])
                    if run is None:
                        await self._exit_tenant()
                        continue
                    # The claim was a Core UPDATE, so a run already in the session
                    # identity map still shows its old status — refresh from the DB
                    # before re-asserting we truly own the (now 'running') claim.
                    await self._session.refresh(run)
                    if run.status != "running":
                        await self._exit_tenant()
                        continue
                    counters["actions"] += await self._resume_run(run)
                await self._exit_tenant()
                counters["resumed"] += 1
            except Exception:  # noqa: BLE001 - one bad run mustn't sink the sweep
                await self._exit_tenant()
                logger.exception("failed to resume run %s", row["id"])
                await self._mark_run_failed(row["id"], row["created_at"], "resume failed")
        return counters

    async def _mark_run_failed(self, run_id: uuid.UUID, created_at: datetime, error: str) -> None:
        """Best-effort privileged UPDATE to fail a run whose savepoint rolled back
        (the ORM object is detached, so we can't flush it)."""
        await self._session.execute(
            text(
                "UPDATE workflow_runs SET status='failed', finished_at=now(), error=:err "
                "WHERE id=:id AND created_at=:ca"
            ),
            {"err": error, "id": run_id, "ca": created_at},
        )

    async def _resume_run(self, run: WorkflowRun) -> int:
        org_id = run.org_id
        version = await WorkflowVersionRepository(self._session, org_id).get(run.workflow_version_id)
        workflow = await WorkflowRepository(self._session, org_id).get(run.workflow_id)
        if version is None or workflow is None:
            await self._finish_run(run, status="failed", error="workflow/version missing on resume")
            return 0
        snapshot = run.input_snapshot or {}
        before = snapshot.get("before")
        after = snapshot.get("after")
        # Manual-run input variables persist on the snapshot; carry them across the
        # delay so post-delay conditions/actions still resolve ``inputs.<key>`` and
        # ``{{ inputs.<key> }}`` (the token engine re-derives these per dispatch; the
        # legacy walker rebuilds the context here and must not drop them).
        inputs = snapshot.get("inputs") or {}
        result = evaluate_graph(
            version.definition,
            {"before": before, "after": after, "inputs": inputs},
            start_node_id=run.resume_node_id,
        )
        if result.error is not None:
            await self._finish_run(run, status="failed", error=result.error)
            return 0
        # Continue step numbering after the steps already recorded pre-delay.
        offset = len(await WorkflowRunRepository(self._session, org_id).steps_for_run(run.id))
        # Cycle guard: a graph like a1 -> delay -> a2 -> a1 would resuspend and
        # resume forever (each resume gets a fresh visited set). Once a run has
        # accumulated MAX_RUN_STEPS across its resume cycles, finish it as failed
        # rather than parking it at the delay again.
        if offset >= MAX_RUN_STEPS:
            await self._finish_run(run, status="failed", error=f"max run steps {MAX_RUN_STEPS} exceeded (delay cycle?)")
            return 0
        event = {
            "id": run.outbox_id,
            "seq": run.outbox_seq,
            "record_id": run.record_id,
            "before_data": before,
            "after_data": after,
            "inputs": inputs,
            "entity_definition_id": workflow.entity_definition_id,
            "entity_table": "",
        }
        # Clear the parked state so a re-suspend (another delay) can set it afresh.
        run.status = "running"
        run.resume_at = None  # type: ignore[assignment]
        run.resume_node_id = None
        await self._session.flush()
        executed, ok = await self._run_actions(org_id, run, event, result.actions, step_offset=offset)
        await self._settle_run(run, result, ok)
        return executed

    async def run_due_schedules(self, *, limit: int = 100) -> dict[str, int]:
        """Fire workflows whose published trigger carries a due interval schedule.

        A scheduled run has no triggering record (``operation="scheduled"``); the
        cadence is derived from the last scheduled run's timestamp, so this is
        safe to sweep at any interval finer than the schedule.

        Concurrency: overlapping beat sweeps must not both fire the same workflow.
        Before firing, we take ``pg_try_advisory_xact_lock(hashtext(workflow_id))``
        — a non-blocking, transaction-scoped lock. Only one sweep wins it per
        workflow per commit window; the other skips. The last-scheduled-run
        timestamp is then re-confirmed under the lock so a run another sweep just
        committed can't be double-fired."""
        rows = (
            await self._session.execute(
                select(Workflow, WorkflowVersion)
                .join(WorkflowVersion, WorkflowVersion.id == Workflow.active_version_id)
                .where(Workflow.enabled.is_(True), WorkflowVersion.status == "published")
            )
        ).all()

        # One grouped query instead of a per-workflow max() (no N+1).
        last_by_wf = await self._last_scheduled_runs()
        now = datetime.now(UTC)

        # Deterministic order: workflows that have never fired first, then the
        # least-recently-fired — so a fixed LIMIT can't starve some schedules.
        _EPOCH = datetime.min.replace(tzinfo=UTC)
        candidates = sorted(rows, key=lambda rv: last_by_wf.get(rv[0].id) or _EPOCH)[:limit]

        counters = {"scheduled": 0, "actions": 0}
        for workflow, version in candidates:
            schedule = _schedule_of(version.definition)
            if schedule is None:
                continue
            last = last_by_wf.get(workflow.id)
            if not is_schedule_due(schedule, last, now):
                continue
            # Non-blocking per-workflow lock (privileged session, cross-org OK).
            locked = (
                await self._session.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:wf))"),
                    {"wf": str(workflow.id)},
                )
            ).scalar_one()
            if not locked:
                continue  # another concurrent sweep is firing this workflow
            # Re-confirm under the lock (guards against a run just committed by a
            # now-finished sweep that our grouped snapshot missed).
            last = await self._last_scheduled_run_at(workflow.org_id, workflow.id)
            if not is_schedule_due(schedule, last, now):
                continue
            try:
                async with self._session.begin_nested():
                    await self._enter_tenant(workflow.org_id)
                    _run, executed = await self.run_version_manually(
                        workflow.org_id,
                        workflow,
                        version,
                        operation="scheduled",
                        record_id=None,
                        before=None,
                        after=None,
                    )
                await self._exit_tenant()
                counters["scheduled"] += 1
                counters["actions"] += executed
            except Exception:  # noqa: BLE001 - one workflow mustn't sink the sweep
                await self._exit_tenant()
                logger.exception("scheduled run failed for workflow %s", workflow.id)
        return counters

    async def _last_scheduled_runs(self) -> dict[uuid.UUID, datetime]:
        """Latest scheduled-run timestamp per workflow, in one grouped query."""
        result = await self._session.execute(
            select(WorkflowRun.workflow_id, func.max(WorkflowRun.created_at))
            .where(WorkflowRun.trigger_operation == "scheduled")
            .group_by(WorkflowRun.workflow_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def _last_scheduled_run_at(self, org_id: uuid.UUID, workflow_id: uuid.UUID) -> datetime | None:
        result = await self._session.execute(
            select(func.max(WorkflowRun.created_at)).where(
                WorkflowRun.org_id == org_id,
                WorkflowRun.workflow_id == workflow_id,
                WorkflowRun.trigger_operation == "scheduled",
            )
        )
        return result.scalar_one_or_none()

    async def _send_email(self, to: str, subject: str, body: str) -> bool:
        """Send a plain email if SMTP is configured; a no-op (False) otherwise."""
        if self._email_sender is None or not self._email_sender.is_configured():
            return False
        await self._email_sender.send(to=to, subject=subject, text=body)
        return True

    async def _mint_form_link(
        self, org_id: uuid.UUID, form_id: uuid.UUID, record_id: uuid.UUID, recipient: str | None
    ) -> tuple[str, bool]:
        """Create an intake-form link for ``record_id`` and email it (send_form action)."""
        from api.schemas.form import GenerateLinkRequest
        from api.services.form_service import FormService

        service = FormService(
            self._session,
            org_id,
            public_base_url=self._public_base_url,
            email_sender=self._email_sender,
        )
        _link, _token, url, email_sent = await service.generate_link(
            form_id, GenerateLinkRequest(target_record_id=record_id, recipient_email=recipient)
        )
        return url, email_sent

    async def _repo_by_id(
        self, org_id: uuid.UUID, definition_id: uuid.UUID, origin_run_id: uuid.UUID
    ) -> DynamicEntityRepository:
        definition = await EntityDefinitionRepository(self._session, org_id).get(definition_id)
        if definition is None:
            raise ActionError("entity definition not found")
        return await self._build_repo(org_id, definition, origin_run_id)

    async def _repo_by_slug(self, org_id: uuid.UUID, slug: str, origin_run_id: uuid.UUID) -> DynamicEntityRepository:
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
