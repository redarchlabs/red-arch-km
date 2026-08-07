"""The workflow side of the agent_task bridge.

Three raw-SQL primitives, all modeled on the engine's ``_signal_parent`` seam
(conditional, idempotent, token-exact):

* :func:`resume_token_for_run` — the wire-back. Called from the agent lifecycle
  choke point in the SAME transaction as the run's terminal status flip, it
  reactivates the exact token parked on this run. The WHERE clause (token id +
  created_at + org + ``status='waiting'`` + ``wait_kind='agent'`` +
  ``correlation_key=<run id>``) makes a late or duplicate signal a no-op and makes
  it impossible to resume a different iteration's park on the same node.
* :func:`reconcile_agent_tokens` — the crash backstop, run on the token-sweep
  beat: any ``agent``-parked token whose linked run is already terminal gets the
  same completion/failure stamp. A lost wire-back degrades to sweep latency,
  never a hang.
* :func:`cancel_runs_for_workflow_run` — cancellation propagation for the
  token-death paths (terminate end event, dead-letter, run failure): linked
  non-terminal agent runs are CAS-cancelled so they stop taking side effects.

This module deliberately imports nothing from the engine (the agents package
reaches it lazily), so there is no import cycle.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentRun

logger = logging.getLogger(__name__)

# Terminal run states the bridge maps onto token routing. "done" completes the
# step; everything else routes to the step's error boundary ("escalated" keeps
# its own error_code so a graph can catch it separately from "failed").
_FAILURE_STATUSES = ("error", "cancelled", "escalated")


def completion_patch(run: AgentRun) -> dict[str, Any]:
    """The token-data stamp for a successfully completed run.

    ``result`` is the schema-validated ``complete_task`` object (what ``capture``
    publishes to vars); the rest is the audit snapshot the step output keeps even
    after either side is retention-pruned.
    """
    return {
        "_completed": True,
        "_completion_output": {
            "result": run.output or {},
            "agent_run_id": str(run.id),
            "agent_id": str(run.agent_id) if run.agent_id else None,
            "status": run.status,
            "total_tokens": run.total_tokens,
            "cost_usd": float(run.cost_usd) if run.cost_usd is not None else None,
        },
    }


def failure_patch(run: AgentRun) -> dict[str, Any]:
    return {
        "_agent_result": {
            "status": run.status,
            "error": run.error or "",
            "agent_run_id": str(run.id),
            "agent_id": str(run.agent_id) if run.agent_id else None,
            "total_tokens": run.total_tokens,
        }
    }


async def resume_token_for_run(
    session: AsyncSession, org_id: uuid.UUID, run: AgentRun, linkage: dict[str, Any]
) -> bool:
    """Reactivate the workflow token parked on ``run`` (terminal). Returns whether
    a token was resumed; ``False`` means the workflow already moved on (timeout
    fired, run terminated) — log and do nothing, the token owner decided."""
    token_id = linkage.get("token_id")
    token_created_at = parse_token_created_at(linkage)
    if not token_id or token_created_at is None:
        return False
    patch = completion_patch(run) if run.status == "done" else failure_patch(run)
    result = await session.execute(
        text(
            "UPDATE workflow_run_tokens "
            "SET status='active', wait_kind=NULL, resume_at=NULL, lease_owner=NULL, leased_at=NULL, "
            "    data = coalesce(data, '{}'::jsonb) || cast(:patch AS jsonb) "
            "WHERE id=:tok AND created_at=:tca AND org_id=:org "
            "  AND status='waiting' AND wait_kind='agent' AND correlation_key=:ck"
        ),
        {
            "patch": json.dumps(patch, default=str),
            "tok": str(token_id),
            "tca": token_created_at,
            "org": str(org_id),
            "ck": str(run.id),
        },
    )
    resumed = int(getattr(result, "rowcount", 0) or 0) > 0
    if not resumed:
        logger.info("agent run %s wire-back late: workflow token already moved on", run.id)
    return resumed


async def reconcile_agent_tokens(session: AsyncSession, *, limit: int = 100) -> int:
    """Cross-org backstop (caller supplies a bypass-scoped session, like the other
    sweeps): stamp + reactivate ``agent``-parked tokens whose linked run is
    terminal but whose wire-back never landed (crash between finalize and signal,
    or an out-of-band admin cancel)."""
    result = await session.execute(
        text(
            """
            UPDATE workflow_run_tokens t
            SET status='active', wait_kind=NULL, resume_at=NULL, lease_owner=NULL, leased_at=NULL,
                data = coalesce(t.data, '{}'::jsonb) || (
                    CASE WHEN r.status = 'done' THEN
                        jsonb_build_object(
                            '_completed', true,
                            '_completion_output', jsonb_build_object(
                                'result', coalesce(r.output, '{}'::jsonb),
                                'agent_run_id', r.id::text,
                                'agent_id', r.agent_id::text,
                                'status', r.status,
                                'total_tokens', r.total_tokens,
                                'cost_usd', r.cost_usd))
                    ELSE
                        jsonb_build_object(
                            '_agent_result', jsonb_build_object(
                                'status', r.status,
                                'error', coalesce(r.error, ''),
                                'agent_run_id', r.id::text,
                                'agent_id', r.agent_id::text,
                                'total_tokens', r.total_tokens))
                    END)
            FROM agent_runs r
            WHERE (t.id, t.created_at) IN (
                SELECT t2.id, t2.created_at
                FROM workflow_run_tokens t2
                JOIN agent_runs r2
                  ON t2.correlation_key = r2.id::text AND t2.org_id = r2.org_id
                WHERE t2.status = 'waiting' AND t2.wait_kind = 'agent'
                  AND r2.status IN ('done', 'error', 'cancelled', 'escalated')
                LIMIT :lim
                FOR UPDATE OF t2 SKIP LOCKED
            )
              AND t.correlation_key = r.id::text AND t.org_id = r.org_id
              AND t.status = 'waiting' AND t.wait_kind = 'agent'
            """
        ),
        {"lim": limit},
    )
    reconciled = int(getattr(result, "rowcount", 0) or 0)
    if reconciled:
        logger.info("reconciled %d agent-parked workflow tokens", reconciled)
    return reconciled


async def cancel_runs_for_workflow_run(
    session: AsyncSession,
    org_id: uuid.UUID,
    workflow_run_id: uuid.UUID,
    *,
    reason: str,
) -> int:
    """CAS-cancel every non-terminal agent run linked to a dying workflow run and
    void their pending approvals (rides ix_agent_runs_workflow_run)."""
    rows = (
        await session.execute(
            text(
                "SELECT id FROM agent_runs WHERE workflow_run_id=:run AND org_id=:org "
                "AND status IN ('queued','running','waiting')"
            ),
            {"run": str(workflow_run_id), "org": str(org_id)},
        )
    ).all()
    if not rows:
        return 0
    from api.repositories.agent_run import AgentRunRepository

    repo = AgentRunRepository(session, org_id)
    cancelled = 0
    for (run_id,) in rows:
        if await repo.cancel_run(run_id, reason=reason):
            cancelled += 1
    return cancelled


def parse_token_created_at(linkage: dict[str, Any]) -> datetime | None:
    try:
        raw = linkage.get("token_created_at")
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None
