"""Terminal transitions for agent runs — the single choke point.

Every path that ends a run (worker completion, executor error, missing
agent/key, approval denial, cancellation, lease expiry) goes through this module
so the compare-and-set semantics and the workflow wire-back cannot be missed by
one call site. ``finalize_run``/``cancel_run`` return whether THIS call won the
terminal transition; on ``False`` the caller takes no further side effects on the
run's behalf.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentRun
from api.repositories.agent_run import AgentRunRepository

logger = logging.getLogger(__name__)


async def finalize_run(
    session: AsyncSession,
    org_id: uuid.UUID,
    run: AgentRun,
    *,
    status: str,
    error: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> bool:
    """CAS-finalize ``run``; on winning, resume any workflow token parked on it."""
    won = await AgentRunRepository(session, org_id).finalize_run(
        run,
        status=status,
        error=error,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    if won:
        await _wire_back(session, org_id, run)
    return won


async def cancel_run(session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, *, reason: str) -> bool:
    """CAS-cancel a run and void its pending approvals.

    Cancellation deliberately does NOT wire back: the canceller (workflow timeout /
    token death / admin) already owns the token's next move, and signalling here
    would race it.
    """
    return await AgentRunRepository(session, org_id).cancel_run(run_id, reason=reason)


async def _wire_back(session: AsyncSession, org_id: uuid.UUID, run: AgentRun) -> None:
    """Resume the workflow token parked on ``run``, if any (same transaction as the
    terminal status flip, so the pair commits or rolls back together)."""
    linkage = (run.input or {}).get("workflow") if isinstance(run.input, dict) else None
    if not linkage:
        return
    # Imported lazily: the workflow engine imports action/tool modules that would
    # otherwise cycle back into the agents package at import time.
    from api.services.workflow.agent_bridge import resume_token_for_run

    try:
        await resume_token_for_run(session, org_id, run, linkage)
    except Exception:  # noqa: BLE001 - reconciliation sweep is the backstop
        logger.exception("agent run %s: workflow wire-back failed (sweep will reconcile)", run.id)
