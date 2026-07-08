"""BPMN token execution engine.

A run advances as durable **tokens** (``workflow_run_tokens``) — today's single
``run.resume_node_id`` cursor generalized to many cursors per run. Tokens are
claimed cross-org with ``FOR UPDATE SKIP LOCKED`` (like the outbox sweep),
advanced one node under a per-run ``pg_try_advisory_xact_lock`` (which serializes
joins + ``step_seq`` allocation so two workers can't double-fire a join), then
re-parked or completed. Parking replaces the legacy run-level suspend with a
per-token wait, so timers, joins, user/receive tasks and (later) retries and
boundary events are all the same durable park-and-resume the ``delay`` node
proved.

Phase 0 scope: trigger fan-out, service/send tasks (via the existing action
registry), exclusive gateways (condition/switch routing), parallel gateways
(fork + AND-join), timer intermediate events (park/resume), end events
(none/terminate), run-scoped variables, and the full claim/lease/advisory-lock
machinery. Retries, error boundaries, inclusive OR-join, user/receive tasks and
connectors layer on in later phases without changing this core.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.workflow import WorkflowRun, WorkflowRunStep, WorkflowRunToken
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowTokenRepository,
    WorkflowVersionRepository,
    json_safe,
)
from api.schemas.workflow_definition import WorkflowDefinitionModel, WorkflowNode
from api.services.workflow import compat
from api.services.workflow import constants as C
from api.services.workflow.decision import evaluate_decision_table
from api.services.workflow.expression import evaluate_transform
from api.services.workflow.jsonlogic import json_logic
from api.services.workflow.retry import (
    attempts_so_far,
    backoff,
    clear_attempts,
    read_policy,
    record_attempt,
)
from api.services.workflow.runner import ActionExecutor

logger = logging.getLogger(__name__)

# Run-wide budgets (generalize the legacy MAX_RUN_STEPS delay-cycle guard). A
# bounded loop is allowed, but these guarantee termination.
MAX_RUN_STEPS = 200
MAX_TOKENS_PER_RUN = 500
MAX_TOKEN_DEPTH = 32
# A 'running' (leased) token older than this is presumed crashed and requeued.
LEASE_TTL_SECONDS = 300

# Wait kinds that an external signal (human completion / message / form submit)
# may resume. Timer/boundary/retry/join resume on their own schedule, not by signal.
_SIGNALABLE_WAIT_KINDS = ("user_task", "receive", "subprocess", "event_based")


@dataclass
class NodeOutcome:
    """What advancing a token at a node produced."""

    kind: str  # advance | emit | park | noop | complete | terminate | fail
    targets: list[tuple[str, str | None]] = field(default_factory=list)  # (node_id, via_handle)
    wait_kind: str | None = None
    resume_at: datetime | None = None
    correlation_key: str | None = None
    token_data: dict[str, Any] | None = None
    variables: dict[str, Any] | None = None
    error: str | None = None


class TokenEngine:
    def __init__(
        self,
        session: AsyncSession,
        *,
        webhook_allowlist: tuple[str, ...] = (),
        trusted_local_hosts: tuple[str, ...] = (),
        public_base_url: str = "",
        email_sender: Any = None,
        org_encryption_key: str = "",
        worker_id: str | None = None,
    ) -> None:
        self._session = session
        self._executor = ActionExecutor(
            session,
            webhook_allowlist=webhook_allowlist,
            trusted_local_hosts=trusted_local_hosts,
            public_base_url=public_base_url,
            email_sender=email_sender,
            org_encryption_key=org_encryption_key,
        )
        self._worker_id = worker_id or f"engine-{uuid.uuid4().hex[:8]}"

    # ---- per-tenant RLS downgrade (mirrors the dispatcher) --------------- #
    async def _enter_tenant(self, org_id: uuid.UUID) -> None:
        await self._session.execute(text("SET LOCAL ROLE app_user"))
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(org_id)}
        )

    async def _exit_tenant(self) -> None:
        await self._session.execute(text("RESET ROLE"))

    async def _lock_run(self, run_id: uuid.UUID) -> bool:
        """Non-blocking per-run advisory lock: serializes all of a run's token
        advances/joins/step_seq within a transaction. Another worker holding it
        skips this token (left ``active``) and retries next sweep."""
        result = await self._session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:run))"), {"run": str(run_id)}
        )
        return bool(result.scalar_one())

    # ---- run startup ----------------------------------------------------- #
    async def start_run(self, run: WorkflowRun, definition: dict[str, Any]) -> None:
        """Seed a run with one active token at each trigger node (already inside
        the event's tenant scope). The trigger node fans out on first advance."""
        model = compat.normalize(definition)
        tokens = WorkflowTokenRepository(self._session, run.org_id)
        triggers = [n for n in model.nodes if n.type == C.NODE_TRIGGER]
        if not triggers:
            await self._fail_run(run, "no trigger node")
            return
        for trigger in triggers:
            await tokens.create(run=run, node_id=trigger.id)

    async def drive_run(self, run: WorkflowRun, *, max_passes: int = 2000) -> dict[str, int]:
        """Advance ONE run's tokens to quiescence within the caller's transaction
        and tenant scope (synchronous dispatch / manual run / tests).

        Unlike :meth:`advance_tokens`, there is no cross-run claim or per-token
        savepoint — a single run runs single-threaded here, so a failing token
        propagates to the caller's savepoint (matching the legacy poison-event
        handling). Parked tokens (timers/receive/join-in-progress) end a pass.

        If the per-run advisory lock is already held (the background token sweep
        is advancing this run right now — reachable when :meth:`retry_run`
        re-drives a run that still has live tokens), we do NOT drive it unlocked:
        two writers would race the run's status/step_seq/tokens. We return
        ``{"skipped": 1}`` and leave progress to the worker that holds the lock.
        A freshly-created run (synchronous dispatch / manual run) is never
        contended, so this only ever short-circuits the concurrent-retry case."""
        tokens = WorkflowTokenRepository(self._session, run.org_id)
        if not await self._lock_run(run.id):
            return {"skipped": 1}
        totals: dict[str, int] = {}
        for _ in range(max_passes):
            active = [t for t in await tokens.list_for_run(run.id) if t.status == "active"]
            if not active:
                break
            for token in active:
                token.status = "running"
                await self._session.flush()
                delta = await self._advance_one(token)
                for key, value in delta.items():
                    totals[key] = totals.get(key, 0) + value
        return totals

    async def retry_run(self, run: WorkflowRun, definition: dict[str, Any]) -> dict[str, int]:
        """Reactivate a failed run's dead tokens and re-drive it (caller owns the
        transaction + tenant scope, like :meth:`drive_run`).

        Each dead token re-enters the node it failed on, so this retries the
        failed step(s) rather than replaying the whole run. Returns ``reactivated``
        (0 = nothing was retryable) plus the advance counters. The caller passes
        the run's own version ``definition`` so a retry executes the exact graph
        the original run did.
        """
        tokens = WorkflowTokenRepository(self._session, run.org_id)
        reactivated = await tokens.reactivate_dead(run.id)
        if reactivated == 0:
            return {"reactivated": 0}
        # Reopen the run so _settle_run can move it back to a terminal state once
        # the reactivated tokens quiesce.
        run.status = "running"
        run.error = None
        run.finished_at = None  # type: ignore[assignment]
        await self._session.flush()
        totals = await self.drive_run(run)
        totals["reactivated"] = reactivated
        return totals

    async def signal_token(
        self,
        run: WorkflowRun,
        *,
        node_id: str | None = None,
        correlation_key: str | None = None,
        variables: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> bool:
        """Complete a parked wait-state token — the primitive behind human task
        completion, message/receive correlation, and form-submission resume.

        Finds one parked wait token for this run (optionally narrowed by
        ``node_id`` or ``correlation_key``), merges ``variables`` into the run's
        variables (so downstream gateways can route on the outcome, e.g. an
        approval decision), stamps a ``_completed`` marker + ``output`` on the
        token, and reactivates it. The next :meth:`drive_run` advances it past the
        task (see the wait-state branch of ``_dispatch_task``). Returns ``True`` if
        a token was signaled. Caller owns the transaction + tenant scope.
        """
        tokens = WorkflowTokenRepository(self._session, run.org_id)
        waiting = [
            t
            for t in await tokens.list_for_run(run.id)
            if t.status == "waiting" and t.wait_kind in _SIGNALABLE_WAIT_KINDS
        ]
        if node_id is not None:
            waiting = [t for t in waiting if t.node_id == node_id]
        if correlation_key is not None:
            waiting = [t for t in waiting if t.correlation_key == correlation_key]
        if not waiting:
            return False
        token = waiting[0]
        if variables:
            await WorkflowRunRepository(self._session, run.org_id).set_variables(run, variables)
        token.data = {**(token.data or {}), "_completed": True, "_completion_output": output or {}}
        token.status = "active"
        token.wait_kind = None
        token.lease_owner = None
        token.leased_at = None
        await self._session.flush()
        return True

    # ---- the sweep ------------------------------------------------------- #
    async def advance_tokens(self, *, limit: int = 100) -> dict[str, int]:
        """Claim a cross-org batch of active tokens and advance each one node."""
        claimed = (
            (
                await self._session.execute(
                    text(
                        """
                    UPDATE workflow_run_tokens t
                    SET status='running', lease_owner=:owner, leased_at=now()
                    WHERE (t.id, t.created_at) IN (
                        SELECT id, created_at FROM workflow_run_tokens
                        WHERE status='active'
                        ORDER BY seq
                        LIMIT :lim
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING t.id, t.created_at, t.org_id, t.run_id, t.run_created_at
                    """
                    ),
                    {"owner": self._worker_id, "lim": limit},
                )
            )
            .mappings()
            .all()
        )

        counters: dict[str, int] = {"claimed": len(claimed), "advanced": 0, "parked": 0, "failed": 0}
        for row in claimed:
            org_id = row["org_id"]
            try:
                async with self._session.begin_nested():
                    await self._enter_tenant(org_id)
                    if not await self._lock_run(row["run_id"]):
                        # Another worker owns this run right now — release the lease.
                        await self._release_token(row["id"], row["created_at"])
                    else:
                        delta = await self._advance_claimed(org_id, row)
                        for key, value in delta.items():
                            counters[key] = counters.get(key, 0) + value
                await self._exit_tenant()
            except Exception:  # noqa: BLE001 - one bad token must not sink the sweep
                await self._exit_tenant()
                logger.exception("token %s advance failed", row.get("id"))
                await self._kill_token(row["id"], row["created_at"], "advance error")
        return counters

    async def resume_due_tokens(self, *, limit: int = 100) -> dict[str, int]:
        """Reactivate parked tokens whose wait elapsed (timers/boundaries/retries)
        and crashed 'running' leases past the TTL. Mirrors resume_waiting_runs."""
        now = datetime.now(UTC)
        claimed = (
            (
                await self._session.execute(
                    text(
                        """
                    UPDATE workflow_run_tokens t
                    SET status='active', lease_owner=NULL, leased_at=NULL
                    WHERE (t.id, t.created_at) IN (
                        SELECT id, created_at FROM workflow_run_tokens
                        WHERE (
                                status='waiting'
                                AND wait_kind IN ('timer','boundary','retry','user_task','receive')
                                AND resume_at <= :now
                              )
                           OR (status='running' AND leased_at < :stale)
                        ORDER BY resume_at NULLS FIRST
                        LIMIT :lim
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING t.id
                    """
                    ),
                    {"now": now, "stale": now - timedelta(seconds=LEASE_TTL_SECONDS), "lim": limit},
                )
            )
            .mappings()
            .all()
        )
        return {"reactivated": len(claimed)}

    async def _release_token(self, token_id: uuid.UUID, created_at: datetime) -> None:
        await self._session.execute(
            text(
                "UPDATE workflow_run_tokens SET status='active', lease_owner=NULL, leased_at=NULL "
                "WHERE id=:id AND created_at=:ca"
            ),
            {"id": token_id, "ca": created_at},
        )

    async def _kill_token(self, token_id: uuid.UUID, created_at: datetime, error: str) -> None:
        """Privileged best-effort fail after a savepoint rollback detached the ORM
        object; also fails the owning run."""
        row = (
            (
                await self._session.execute(
                    text(
                        "UPDATE workflow_run_tokens SET status='dead', finished_at=now(), "
                        "data = coalesce(data,'{}'::jsonb) || jsonb_build_object('_error', cast(:err AS text)) "
                        "WHERE id=:id AND created_at=:ca RETURNING run_id, run_created_at, org_id"
                    ),
                    {"err": error, "id": token_id, "ca": created_at},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            await self._session.execute(
                text(
                    "UPDATE workflow_runs SET status='failed', finished_at=now(), error=:err "
                    "WHERE id=:run AND created_at=:rca AND status NOT IN ('succeeded','skipped')"
                ),
                {"err": error, "run": row["run_id"], "rca": row["run_created_at"]},
            )

    # ---- advance one claimed token --------------------------------------- #
    async def _advance_claimed(self, org_id: uuid.UUID, row: dict[str, Any]) -> dict[str, int]:
        token = await WorkflowTokenRepository(self._session, org_id).get(row["id"], row["created_at"])
        if token is None or token.status != "running":
            return {}
        return await self._advance_one(token)

    async def _advance_one(self, token: WorkflowRunToken) -> dict[str, int]:
        """Advance a single token one node. The caller owns the transaction +
        tenant scope + any concurrency claim; this is the pure control-flow step."""
        org_id = token.org_id
        tokens = WorkflowTokenRepository(self._session, org_id)
        runs = WorkflowRunRepository(self._session, org_id)
        run = await runs.get(token.run_id, token.run_created_at)
        if run is None:
            token.status = "dead"
            await self._session.flush()
            return {}
        version = await WorkflowVersionRepository(self._session, org_id).get(run.workflow_version_id)
        if version is None:
            await self._fail_run(run, "workflow version missing")
            token.status = "dead"
            await self._session.flush()
            return {"failed": 1}

        model = compat.normalize(version.definition)
        node = model.node_by_id(token.node_id)
        if node is None:
            token.status = "dead"
            await self._session.flush()
            await self._settle_run(run, tokens)
            return {}

        outcome = await self._dispatch(node, token, run, model, tokens, runs)
        counter = await self._apply(outcome, node, token, run, model, tokens)
        await self._settle_run(run, tokens)
        return counter

    # ---- node dispatch --------------------------------------------------- #
    async def _dispatch(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        tokens: WorkflowTokenRepository,
        runs: WorkflowRunRepository,
    ) -> NodeOutcome:
        if node.type == C.NODE_TRIGGER:
            return NodeOutcome("advance", targets=_out_edges(model, node.id))

        if node.type == C.NODE_GATEWAY:
            return await self._dispatch_gateway(node, token, run, model, tokens)

        if node.type == C.NODE_EVENT:
            return self._dispatch_event(node, token, model)

        if node.type == C.NODE_TASK:
            return await self._dispatch_task(node, token, run, model, runs)

        # Unknown category — forward (matches the legacy passthrough behavior).
        return NodeOutcome("advance", targets=_out_edges(model, node.id))

    async def _dispatch_task(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        runs: WorkflowRunRepository,
    ) -> NodeOutcome:
        task_type = node.task_type
        # Wait-state tasks (human task / receive / call / manual) block on an
        # external signal. On first arrival the token PARKS; signal_token() sets a
        # `_completed` marker + reactivates it, and the re-dispatch below advances
        # past the task (recording the completion), consuming the marker. This
        # mirrors the timer `_timer_armed` pattern.
        if task_type in C.WAIT_TASK_TYPES:
            data = token.data or {}
            if data.get("_completed"):
                output = data.get("_completion_output") or {"completed": True}
                cleaned = {k: v for k, v in data.items() if k not in ("_completed", "_completion_output", "_armed")}
                await self._record_step(run, node, token, status="succeeded", output=output)
                variables = None
                capture = node.data.get("capture")
                if isinstance(capture, str) and capture:
                    variables = {capture: output}
                return NodeOutcome(
                    "advance", targets=_out_edges(model, node.id), token_data=cleaned, variables=variables
                )
            # Call activity / sub-process: run a child workflow (not a passive wait).
            if task_type in (C.TASK_CALL, C.TASK_SUBPROCESS):
                return await self._dispatch_call(node, token, run, model, runs)
            wait_kind = {
                C.TASK_USER: "user_task",
                C.TASK_RECEIVE: "receive",
                C.TASK_MANUAL: "user_task",
            }[task_type]
            # Timer/escalation boundary: an armed token re-dispatched WITHOUT a
            # completion marker means its SLA timer fired — route to the
            # (interrupting) boundary's escalation path. Completion above always
            # wins the race.
            if data.get("_armed"):
                boundary = _timer_boundary_for(model, node.id)
                if boundary is not None:
                    await self._record_step(run, node, token, status="skipped", output={"timed_out": True})
                    cleaned = {k: v for k, v in data.items() if k != "_armed"}
                    return NodeOutcome(
                        "advance",
                        targets=_out_edges(model, boundary.id),
                        token_data={**cleaned, "_error": {"timeout": node.id}},
                    )
            # First arrival: if a timer boundary is attached, park with an SLA
            # deadline (the timer sweep reactivates it on expiry); else park open.
            boundary = _timer_boundary_for(model, node.id)
            if boundary is not None:
                delay = int((boundary.data or {}).get("delay_seconds", 0) or 0)
                return NodeOutcome(
                    "park",
                    wait_kind=wait_kind,
                    resume_at=datetime.now(UTC) + timedelta(seconds=max(0, delay)),
                    token_data={**data, "_armed": True},
                )
            return NodeOutcome("park", wait_kind=wait_kind)

        if task_type == C.TASK_BUSINESS_RULE:
            return await self._dispatch_decision(node, token, run, model, runs)

        if task_type == C.TASK_SCRIPT:
            return await self._dispatch_script(node, token, run, model, runs)

        action_type = node.data.get("action_type")
        if not action_type:
            # A script/business-rule/service task without a wired handler yet:
            # record a skipped step and continue rather than failing the run.
            await self._record_step(run, node, token, status="skipped", output={"note": "task type not yet supported"})
            return NodeOutcome("advance", targets=_out_edges(model, node.id))

        # Run-wide step budget (bounds loops + fan-out).
        if run.step_seq >= MAX_RUN_STEPS:
            return NodeOutcome("fail", error=f"max run steps {MAX_RUN_STEPS} exceeded")

        step = await self._record_step(run, node, token, status="running")
        snapshot = run.input_snapshot or {}
        result = await self._executor.execute(
            org_id=run.org_id,
            action_type=str(action_type),
            config=node.data.get("config", {}) or {},
            record_id=run.record_id,
            before=snapshot.get("before"),
            after=snapshot.get("after"),
            inputs=snapshot.get("inputs") or {},
            entity_definition_id=await self._entity_of(run),
            origin_run_id=run.id,
        )
        if result.ok:
            step.status = "succeeded"
            step.output = result.output
            step.finished_at = func.now()
            await self._session.flush()
            # A task that needed retries clears its counter on success so a later
            # loop back through this node re-arms a fresh retry budget.
            cleared = clear_attempts(token.data, node.id)
            token_data = cleared if cleared is not token.data else None
            # Optional capture: publish this task's output as a run variable so a
            # downstream gateway/task can use it (e.g. an http_request response).
            variables = None
            capture = node.data.get("capture")
            if isinstance(capture, str) and capture:
                variables = {capture: result.output}
            return NodeOutcome(
                "advance", targets=_out_edges(model, node.id), token_data=token_data, variables=variables
            )

        # Failure. Retry is opt-in via node.data.retry; without a policy this is a
        # 1-attempt no-op and the behaviour matches the legacy walker exactly.
        policy = read_policy(node.data)
        attempt = attempts_so_far(token.data, node.id)  # failures so far (0-based)
        if attempt + 1 < policy.max_attempts:
            delay = backoff(attempt, policy)
            resume_at = datetime.now(UTC) + timedelta(seconds=delay)
            step.status = "retrying"
            step.error = result.error
            step.attempts = attempt + 1
            step.max_attempts = policy.max_attempts
            step.next_retry_at = resume_at
            step.finished_at = func.now()
            await self._session.flush()
            logger.info(
                "workflow task retry %d/%d (run=%s node=%s) in %.1fs: %s",
                attempt + 1,
                policy.max_attempts,
                run.id,
                node.id,
                delay,
                result.error,
            )
            return NodeOutcome(
                "park",
                wait_kind="retry",
                resume_at=resume_at,
                token_data=record_attempt(token.data, node.id, attempt + 1),
            )

        # No (further) retries: record the terminal failure.
        step.status = "failed"
        step.error = result.error
        step.attempts = attempt + 1
        step.max_attempts = policy.max_attempts
        step.finished_at = func.now()
        await self._session.flush()
        logger.info("workflow task failed (run=%s node=%s): %s", run.id, node.id, result.error)
        # BPMN try/catch: if an error boundary event is attached to this task, the
        # failure is CAUGHT — route the token to the boundary node (which then
        # follows its error path) instead of failing the run. The error context
        # travels on the token so the handler branch can read it.
        # error_code is not surfaced by handlers yet, so today every error boundary
        # is a catch-all; the plumbing is ready for code-specific catches.
        error_code = getattr(result, "error_code", None)
        boundary = _error_boundary_for(model, node.id, error_code)
        if boundary is not None:
            error_ctx = {"node": node.id, "message": result.error, "code": error_code}
            logger.info("workflow error caught by boundary %s (run=%s node=%s)", boundary.id, run.id, node.id)
            return NodeOutcome(
                "advance",
                targets=[(boundary.id, C.HANDLE_BOUNDARY)],
                token_data={**(token.data or {}), "_error": error_ctx},
            )
        # continue_on_error swallows the failure and follows the normal out-edge.
        if node.data.get("continue_on_error", False):
            return NodeOutcome("advance", targets=_out_edges(model, node.id))
        # Exhausted, uncaught, not continuing → dead-letter the run so it surfaces
        # in the DLQ view and can be replayed via retry_workflow_run.
        run.dead_letter = True
        return NodeOutcome("fail", error=result.error)

    async def _dispatch_decision(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        runs: WorkflowRunRepository,
    ) -> NodeOutcome:
        """A businessRule (decision-table) task: derive output values from the run
        context and publish them as run variables so a downstream gateway can route
        on them. Side-effect-free (pure jsonlogic), so it never fails the run."""
        if run.step_seq >= MAX_RUN_STEPS:
            return NodeOutcome("fail", error=f"max run steps {MAX_RUN_STEPS} exceeded")
        outputs = evaluate_decision_table(node.data.get("decision_table"), _expr_context(run))
        await self._record_step(run, node, token, status="succeeded", output=outputs)
        return NodeOutcome(
            "advance",
            targets=_out_edges(model, node.id),
            variables=outputs or None,
        )

    async def _dispatch_script(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        runs: WorkflowRunRepository,
    ) -> NodeOutcome:
        """A script/transform task: map ``data.transform`` ({var: jsonlogic-expr})
        over the run context and publish the results as run variables. Sandboxed
        (jsonlogic only — no arbitrary code) and side-effect-free, so it never
        fails the run."""
        if run.step_seq >= MAX_RUN_STEPS:
            return NodeOutcome("fail", error=f"max run steps {MAX_RUN_STEPS} exceeded")
        outputs = evaluate_transform(node.data.get("transform"), _expr_context(run))
        await self._record_step(run, node, token, status="succeeded", output=outputs)
        return NodeOutcome(
            "advance",
            targets=_out_edges(model, node.id),
            variables=outputs or None,
        )

    async def _dispatch_call(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        runs: WorkflowRunRepository,
    ) -> NodeOutcome:
        """Call activity: start a CHILD workflow run and block on it.

        The child is created + driven inline (it's freshly created, so its per-run
        advisory lock never contends with the parent's). If the child runs to
        completion, the call task advances immediately with the child's variables
        (optionally captured); a failed child is routed like any task failure
        (error boundary / continue / dead-letter). If the child parks on its own
        wait (a nested human task), the parent parks too and ``_signal_parent``
        resumes it when the child later completes. ``MAX_TOKEN_DEPTH`` bounds
        recursion.
        """
        if run.depth >= MAX_TOKEN_DEPTH:
            return NodeOutcome("fail", error=f"call depth {MAX_TOKEN_DEPTH} exceeded")
        try:
            target_id = uuid.UUID(str(node.data.get("call_workflow_id")))
        except (ValueError, TypeError):
            return NodeOutcome("fail", error="call task requires a valid call_workflow_id")
        target = await WorkflowRepository(self._session, run.org_id).get(target_id)
        if target is None or target.active_version_id is None:
            return NodeOutcome("fail", error="call target workflow not found or has no published version")
        version = await WorkflowVersionRepository(self._session, run.org_id).get(target.active_version_id)
        if version is None or version.status != "published":
            return NodeOutcome("fail", error="call target has no published version")

        snapshot = run.input_snapshot or {}
        child = await runs.create_run_if_absent(
            workflow_id=target.id,
            workflow_version_id=version.id,
            outbox_id=uuid.uuid4(),
            outbox_seq=None,
            created_at=datetime.now(UTC),
            trigger_operation="call",
            record_id=run.record_id,
            input_snapshot={
                "before": snapshot.get("before"),
                "after": snapshot.get("after"),
                # Carry the parent's manual-run inputs so a called sub-process can
                # also resolve ``inputs.<key>`` (the primary inter-run channel is
                # still ``vars`` via a task's ``capture``).
                "inputs": snapshot.get("inputs") or {},
                "vars": run.variables or {},
            },
            depth=run.depth + 1,
            parent_run_id=run.id,
            parent_token_id=token.id,
        )
        if child is None:
            return NodeOutcome("fail", error="could not create child run")
        await self.start_run(child, version.definition)
        await self.drive_run(child)  # child is new → its advisory lock is uncontended
        fresh_child = await runs.get(child.id, child.created_at)
        child_vars = (fresh_child.variables or {}) if fresh_child else {}
        status = fresh_child.status if fresh_child else "failed"

        if status in ("succeeded", "skipped"):
            output = {"child_run_id": str(child.id), "vars": child_vars}
            await self._record_step(run, node, token, status="succeeded", output=output)
            variables = None
            capture = node.data.get("capture")
            if isinstance(capture, str) and capture:
                variables = {capture: child_vars}
            return NodeOutcome("advance", targets=_out_edges(model, node.id), variables=variables)

        if status == "failed":
            await self._record_step(run, node, token, status="failed", output={"child_run_id": str(child.id)})
            boundary = _error_boundary_for(model, node.id)
            if boundary is not None:
                return NodeOutcome(
                    "advance",
                    targets=[(boundary.id, C.HANDLE_BOUNDARY)],
                    token_data={**(token.data or {}), "_error": {"node": node.id, "message": "child run failed"}},
                )
            if node.data.get("continue_on_error", False):
                return NodeOutcome("advance", targets=_out_edges(model, node.id))
            run.dead_letter = True
            return NodeOutcome("fail", error="child run failed")

        # Child is still running/waiting (a nested human task/timer): park; the
        # child's completion will reactivate this token via _signal_parent.
        return NodeOutcome(
            "park", wait_kind="subprocess", token_data={**(token.data or {}), "_child_run_id": str(child.id)}
        )

    async def _dispatch_gateway(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        tokens: WorkflowTokenRepository,
    ) -> NodeOutcome:
        gateway_type = node.gateway_type or C.GATEWAY_EXCLUSIVE
        incoming = _incoming_edges(model, node.id)

        # Converging parallel/inclusive gateway (>=2 incoming) = a JOIN.
        if gateway_type in C.FORKING_GATEWAY_TYPES and len(incoming) >= 2:
            if gateway_type == C.GATEWAY_INCLUSIVE:
                # OR-join: fire once no other live token can still reach us (dead-
                # path aware), so it converges correctly after an exclusive split.
                return await self._inclusive_join(node, token, run, model, tokens)
            return await self._parallel_join(node, token, run, model, tokens, incoming)

        if gateway_type in C.FORKING_GATEWAY_TYPES:
            # Diverging fork: emit a token on every outgoing edge. (An inclusive
            # fork with per-branch conditions is a later refinement; emit-all is a
            # safe superset — the reachability OR-join still converges correctly.)
            return NodeOutcome("advance", targets=_out_edges(model, node.id))

        # Exclusive (and event-based routing / condition / switch / passthrough).
        return self._exclusive_route(node, run, model)

    def _exclusive_route(self, node: WorkflowNode, run: WorkflowRun, model: WorkflowDefinitionModel) -> NodeOutcome:
        outs = _out_edges(model, node.id)
        if not outs:
            return NodeOutcome("complete")
        ctx = _expr_context(run)
        data = node.data or {}
        chosen: str | None
        if "cases" in data and data.get("cases"):
            chosen = "default"
            for case in data.get("cases", []):
                expr = case.get("expr")
                if expr is None or bool(json_logic(expr, ctx)):
                    chosen = case.get("handle")
                    break
        elif "expr" in data and data.get("expr") is not None:
            chosen = "true" if bool(json_logic(data.get("expr"), ctx)) else "false"
        else:
            # Plain passthrough/merge: take the sole (or first) out edge.
            return NodeOutcome("advance", targets=[outs[0]])

        target = next((t for t in outs if (t[1] or C.HANDLE_DEFAULT) == chosen), None)
        if target is None and chosen != C.HANDLE_DEFAULT:
            target = next((t for t in outs if (t[1] or C.HANDLE_DEFAULT) == C.HANDLE_DEFAULT), None)
        if target is None:
            # No matching branch and no default → this path stops here.
            return NodeOutcome("complete")
        return NodeOutcome("advance", targets=[target])

    async def _parallel_join(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        tokens: WorkflowTokenRepository,
        incoming: list[tuple[str, str | None]],
    ) -> NodeOutcome:
        """AND-join: buffer the arriving token; fire only once every incoming edge
        has a buffered token. Safe under the per-run advisory lock (no race)."""
        token.status = "waiting"
        token.wait_kind = "join"
        token.finished_at = None
        await self._session.flush()

        buffered = await tokens.buffered_at(run.id, node.id, "join")
        have = {(t.arrived_from_node_id, t.arrived_via_handle) for t in buffered}
        need = set(incoming)
        if not need <= have:
            return NodeOutcome("noop")  # still waiting on other branches

        # Fire: consume the whole buffer, emit one token on each outgoing edge.
        for buffered_token in buffered:
            buffered_token.status = "dead"
            buffered_token.finished_at = func.now()
        await self._session.flush()
        return NodeOutcome("emit", targets=_out_edges(model, node.id))

    async def _inclusive_join(
        self,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        tokens: WorkflowTokenRepository,
    ) -> NodeOutcome:
        """Reachability (dead-path-aware) OR-join: buffer the arriving token, then
        fire once NO other live token in the run can still reach this join.

        Unlike the AND-join it does not wait for every incoming edge — only for the
        branches that actually carry a token. This makes it converge correctly
        after an exclusive split (where some incoming edges never fire), which an
        AND-join would deadlock on. Runs under the per-run advisory lock, so the
        live-token snapshot is race-free.
        """
        token.status = "waiting"
        token.wait_kind = "join"
        token.finished_at = None
        await self._session.flush()

        # Live tokens that are NOT already buffered at this join. If any of them can
        # still reach this join node, more tokens may yet arrive — keep waiting.
        others = [
            t
            for t in await tokens.list_for_run(run.id)
            if t.status in ("active", "running", "waiting") and not (t.node_id == node.id and t.wait_kind == "join")
        ]
        reachable = _forward_reachable_nodes(model, {t.node_id for t in others})
        if node.id in reachable:
            return NodeOutcome("noop")

        # No more tokens can arrive: fire with whatever converged.
        buffered = await tokens.buffered_at(run.id, node.id, "join")
        for buffered_token in buffered:
            buffered_token.status = "dead"
            buffered_token.finished_at = func.now()
        await self._session.flush()
        return NodeOutcome("emit", targets=_out_edges(model, node.id))

    def _dispatch_event(
        self, node: WorkflowNode, token: WorkflowRunToken, model: WorkflowDefinitionModel
    ) -> NodeOutcome:
        data = node.data or {}
        position = data.get("position", C.EVENT_INTERMEDIATE)

        if position == C.EVENT_END:
            end_type = data.get("end_type") or data.get("event_type") or C.EVENT_NONE
            if end_type == C.EVENT_TERMINATE:
                return NodeOutcome("terminate")
            if end_type == C.EVENT_ERROR:
                # Throw an error end: in a flat graph (no enclosing scope to catch
                # it yet — subprocess propagation is a later phase) this fails the
                # run with the modeled error code.
                code = data.get("error_code") or "error"
                return NodeOutcome("fail", error=f"error end event ({code})")
            return NodeOutcome("complete")

        if position == C.EVENT_INTERMEDIATE:
            event_type = data.get("event_type")
            if event_type == C.EVENT_TIMER:
                if (token.data or {}).get("_timer_armed"):
                    # The timer sweep reactivated us — the wait elapsed; move on.
                    new_data = {k: v for k, v in (token.data or {}).items() if k != "_timer_armed"}
                    return NodeOutcome("advance", targets=_out_edges(model, node.id), token_data=new_data)
                delay = int(data.get("delay_seconds", 0) or 0)
                return NodeOutcome(
                    "park",
                    wait_kind="timer",
                    resume_at=datetime.now(UTC) + timedelta(seconds=max(0, delay)),
                    token_data={**(token.data or {}), "_timer_armed": True},
                )
            if event_type in (C.EVENT_MESSAGE, C.EVENT_SIGNAL):
                # Catch: park until correlated (receive machinery, later phase).
                return NodeOutcome("park", wait_kind="receive")
            # Throw / none intermediate: pass through.
            return NodeOutcome("advance", targets=_out_edges(model, node.id))

        # A boundary event only receives a token when its host activity fired it
        # (e.g. an error boundary caught a task failure — see _dispatch_task). When
        # reached, follow its outgoing (error/timeout-handling) path. Boundaries
        # have no normal incoming edges, so this is only hit via that routing.
        return NodeOutcome("advance", targets=_out_edges(model, node.id))

    # ---- apply an outcome ------------------------------------------------ #
    async def _apply(
        self,
        outcome: NodeOutcome,
        node: WorkflowNode,
        token: WorkflowRunToken,
        run: WorkflowRun,
        model: WorkflowDefinitionModel,
        tokens: WorkflowTokenRepository,
    ) -> dict[str, int]:
        if outcome.variables:
            await WorkflowRunRepository(self._session, run.org_id).set_variables(run, outcome.variables)

        if outcome.kind == "noop":
            return {"parked": 1}

        if outcome.kind == "park":
            token.status = "waiting"
            token.wait_kind = outcome.wait_kind
            token.resume_at = outcome.resume_at  # type: ignore[assignment]
            token.correlation_key = outcome.correlation_key
            if outcome.token_data is not None:
                token.data = outcome.token_data
            token.lease_owner = None
            token.leased_at = None
            await self._session.flush()
            return {"parked": 1}

        if outcome.kind == "complete":
            token.status = "completed"
            token.finished_at = func.now()
            await self._session.flush()
            return {"advanced": 1}

        if outcome.kind == "fail":
            token.status = "dead"
            token.finished_at = func.now()
            await self._session.flush()
            await self._fail_run(run, outcome.error or "task failed")
            return {"failed": 1}

        if outcome.kind == "terminate":
            token.status = "completed"
            token.finished_at = func.now()
            await tokens.kill_all(run.id)
            await self._finish_run(run, "succeeded")
            return {"advanced": 1}

        # advance / emit → create tokens at the targets.
        targets = outcome.targets
        if await tokens.token_count(run.id) + len(targets) > MAX_TOKENS_PER_RUN:
            token.status = "dead"
            await self._session.flush()
            await self._fail_run(run, f"max tokens per run {MAX_TOKENS_PER_RUN} exceeded")
            return {"failed": 1}

        if outcome.kind == "advance" and len(targets) == 1:
            # Linear step: reuse the token (preserves branch identity + lineage).
            target, handle = targets[0]
            token.node_id = target
            token.arrived_from_node_id = node.id
            token.arrived_via_handle = handle
            token.status = "active"
            token.lease_owner = None
            token.leased_at = None
            if outcome.token_data is not None:
                token.data = outcome.token_data
            await self._session.flush()
            return {"advanced": 1}

        # Fork (advance to N>1), emit (join fired), or a dead end (0 targets).
        if outcome.kind == "advance":
            token.status = "completed"
            token.finished_at = func.now()
            if outcome.token_data is not None:
                token.data = outcome.token_data
            await self._session.flush()

        for target, handle in targets:
            await tokens.create(
                run=run,
                node_id=target,
                arrived_from_node_id=node.id,
                arrived_via_handle=handle,
                parent_token_id=token.id,
                created_by_node=node.id,
                depth=min(token.depth + (1 if len(targets) > 1 else 0), MAX_TOKEN_DEPTH),
            )
        return {"advanced": 1}

    # ---- run settlement -------------------------------------------------- #
    async def _settle_run(self, run: WorkflowRun, tokens: WorkflowTokenRepository) -> None:
        if run.status in ("succeeded", "failed", "skipped"):
            return
        statuses = [
            r[0]
            for r in (
                await self._session.execute(
                    select(WorkflowRunToken.status).where(
                        WorkflowRunToken.run_id == run.id, WorkflowRunToken.org_id == run.org_id
                    )
                )
            ).all()
        ]
        if any(s in ("active", "running") for s in statuses):
            run.status = "running"
        elif any(s == "waiting" for s in statuses):
            run.status = "waiting"
        else:
            await self._finish_run(run, "succeeded")
            return
        await self._session.flush()

    async def _finish_run(self, run: WorkflowRun, status: str) -> None:
        run.status = status
        run.conditions_matched = True
        run.finished_at = func.now()
        await self._session.flush()
        await self._signal_parent(run)

    async def _fail_run(self, run: WorkflowRun, error: str) -> None:
        run.status = "failed"
        run.error = error
        run.finished_at = func.now()
        await self._session.flush()
        await self._signal_parent(run)

    async def _signal_parent(self, run: WorkflowRun) -> None:
        """If this is a child (call-activity) run, reactivate the parent's parked
        call token so the parent advances. A conditional raw UPDATE — the parent
        token is parked (no concurrent advance of it), so no parent lock is needed;
        it's a no-op in the synchronous case (the parent token isn't parked yet)."""
        if not run.parent_run_id or not run.parent_token_id:
            return
        completion = {"child_run_id": str(run.id), "status": run.status, "vars": run.variables or {}}
        await self._session.execute(
            text(
                "UPDATE workflow_run_tokens "
                "SET status='active', wait_kind=NULL, lease_owner=NULL, leased_at=NULL, "
                "    data = coalesce(data, '{}'::jsonb) || cast(:patch AS jsonb) "
                "WHERE id=:tok AND org_id=:org AND status='waiting' AND wait_kind='subprocess'"
            ),
            {
                "patch": json.dumps({"_completed": True, "_completion_output": json_safe(completion)}),
                "tok": run.parent_token_id,
                "org": run.org_id,
            },
        )

    async def _record_step(
        self,
        run: WorkflowRun,
        node: WorkflowNode,
        token: WorkflowRunToken,
        *,
        status: str,
        output: dict[str, Any] | None = None,
    ) -> WorkflowRunStep:
        runs = WorkflowRunRepository(self._session, run.org_id)
        index = await runs.allocate_step_index(run)
        step = await runs.add_step(
            run=run,
            node_id=node.id,
            action_type=str(node.data.get("action_type") or node.task_type or node.type),
            step_index=index,
            token_id=token.id,
        )
        step.status = status
        if output is not None:
            step.output = output
        if status in ("succeeded", "skipped", "failed"):
            step.finished_at = func.now()
        await self._session.flush()
        return step

    async def _entity_of(self, run: WorkflowRun) -> uuid.UUID | None:
        from api.repositories.workflow import WorkflowRepository

        workflow = await WorkflowRepository(self._session, run.org_id).get(run.workflow_id)
        return workflow.entity_definition_id if workflow is not None else None


# --------------------------------------------------------------------------- #
# graph helpers
# --------------------------------------------------------------------------- #
def _out_edges(model: WorkflowDefinitionModel, node_id: str) -> list[tuple[str, str | None]]:
    return [(e.target, e.source_handle) for e in model.edges if e.source == node_id]


def _forward_reachable_nodes(model: WorkflowDefinitionModel, start_ids: set[str]) -> set[str]:
    """Node ids reachable from any start id by following outgoing edges (BFS over
    the static graph). Used by the inclusive OR-join to decide whether any live
    token could still arrive. Graphs are tiny, so this is cheap."""
    adjacency: dict[str, list[str]] = {}
    for edge in model.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    seen: set[str] = set()
    queue: deque[str] = deque(start_ids)
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(adjacency.get(current, []))
    return seen


def _error_boundary_for(
    model: WorkflowDefinitionModel, node_id: str, error_code: str | None = None
) -> WorkflowNode | None:
    """The error boundary event attached to ``node_id`` that catches this failure.

    A boundary with a specific ``error_code`` catches only that code; a boundary
    with no code is a catch-all. A catch-all is preferred only if no code-specific
    boundary matches, so authors can special-case codes and still have a fallback.
    """
    catch_all: WorkflowNode | None = None
    for node in model.nodes:
        data = node.data or {}
        if (
            node.type == C.NODE_EVENT
            and data.get("position") == C.EVENT_BOUNDARY
            and data.get("event_type") == C.EVENT_ERROR
            and data.get("attached_to") == node_id
        ):
            boundary_code = data.get("error_code")
            if boundary_code and error_code and str(boundary_code) == str(error_code):
                return node
            if not boundary_code and catch_all is None:
                catch_all = node
    return catch_all


def _timer_boundary_for(model: WorkflowDefinitionModel, node_id: str) -> WorkflowNode | None:
    """The timer boundary event attached to ``node_id`` (SLA/escalation), if any."""
    for node in model.nodes:
        data = node.data or {}
        if (
            node.type == C.NODE_EVENT
            and data.get("position") == C.EVENT_BOUNDARY
            and data.get("event_type") == C.EVENT_TIMER
            and data.get("attached_to") == node_id
        ):
            return node
    return None


def _incoming_edges(model: WorkflowDefinitionModel, node_id: str) -> list[tuple[str, str | None]]:
    return [(e.source, e.source_handle) for e in model.edges if e.target == node_id]


def _expr_context(run: WorkflowRun) -> dict[str, Any]:
    snapshot = run.input_snapshot or {}
    return {
        "before": snapshot.get("before"),
        "after": snapshot.get("after"),
        # Caller-supplied manual-run variables (empty for record/form runs), so
        # gateways/scripts can route on ``inputs.<key>``.
        "inputs": snapshot.get("inputs") or {},
        "vars": run.variables or {},
    }
