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
    """CAS-finalize ``run``; on winning, settle its questions and resume any
    workflow token parked on it."""
    won = await AgentRunRepository(session, org_id).finalize_run(
        run,
        status=status,
        error=error,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    if won:
        await _settle_questions(
            session, org_id, run, reason=f"The consulted agent's run ended ({status}) without answering."
        )
        await _wire_back(session, org_id, run)
    return won


async def cancel_run(session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, *, reason: str) -> bool:
    """CAS-cancel a run and void its pending approvals.

    Cancellation deliberately does NOT wire back: the canceller (workflow timeout /
    token death / admin) already owns the token's next move, and signalling here
    would race it. It DOES settle questions — an agent left waiting on a cancelled
    run is stuck on an answer that is never coming.
    """
    won = await AgentRunRepository(session, org_id).cancel_run(run_id, reason=reason)
    if won:
        run = await AgentRunRepository(session, org_id).get_run(run_id)
        if run is not None:
            await _settle_questions(session, org_id, run, reason="The consulted agent's run was cancelled.")
    return won


async def _settle_questions(session: AsyncSession, org_id: uuid.UUID, run: AgentRun, *, reason: str) -> None:
    """Close both sides of the question ledger for a run that just ended.

    As an *answerer*: a consult run that never called ``reply_to_peer`` would leave
    its asker parked indefinitely, so the asker is resumed with an explicit "no
    answer". As an *asker*: its own open questions are voided, because answering one
    later would try to re-queue a run that is already terminal.
    """
    from api.services.agents import questions

    try:
        await questions.settle_for_peer_run(session, org_id, run, reason=reason)
        await questions.void_open_questions(session, org_id, run.id)
    except Exception:  # noqa: BLE001 - never let bookkeeping undo a terminal transition
        logger.exception("agent run %s: settling open questions failed", run.id)


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
