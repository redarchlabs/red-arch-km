"""Convening a review board, and reading its answer — the I/O half of peer review.

The rules live in :mod:`api.services.agents.review_board` and are pure. This is
what talks to the database: it creates the reviewer runs, parks the author on
their answers, and records each verdict in the work-order diary as it lands.

A board is dispatched as **sibling questions on one tool call**, so the author's
run parks once and wakes when the *last* reviewer reports
(:func:`api.services.agents.questions._inject_answer` holds the resume until no
sibling is pending). Four serial consults would cost the same tokens and four
times the wall clock.

Reviewer runs are deliberately unlike delegations in three ways:

* **Plan-only posture**, so a reviewer cannot act on what it is reviewing. That is
  also what makes it safe to seat a non-advisory agent — ``consult_peer`` refuses
  operator targets to stop one being handed work, and buildability review needs
  ``principal-engineer``, an operator.
* **The cheap model** (``agents.review_model``), because reading a plan costs a
  fraction of writing one.
* **A bounded brief**: the work order, the submission and the task list — never
  the author's research transcript. A reviewer that reads the author's reasoning
  tends to adopt it, which is the opposite of why it is there.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from api.models.agent import Agent
from api.models.org import Org
from api.models.work_order import WorkOrder, WorkOrderEntry
from api.repositories.agent_run import AgentRunRepository
from api.services.agents import review_board as rb
from api.services.agents.tools.spec import ToolContext

# Which board an order draws from. Engineering unless the order's own agents are
# the business pod — a judgement the org's config makes, not this module.
_BOARD_BY_KIND = {"business": "business", "engineering": "engineering"}


async def _org_boards(ctx: ToolContext) -> Any:
    org = await ctx.session.get(Org, ctx.org_id)
    return getattr(org, "review_boards", None) if org else None


async def _entries(ctx: ToolContext, wo_id: uuid.UUID) -> list[WorkOrderEntry]:
    rows = (
        (
            await ctx.session.execute(
                select(WorkOrderEntry)
                .where(WorkOrderEntry.work_order_id == wo_id, WorkOrderEntry.org_id == ctx.org_id)
                .order_by(WorkOrderEntry.created_at, WorkOrderEntry.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _seat_agents(ctx: ToolContext, seats: list[rb.Seat]) -> list[tuple[rb.Seat, Agent]]:
    """Pair each seat with a live agent, dropping seats the org does not staff.

    A board naming an agent this org has not created is a configuration gap, not a
    reason to refuse the review — the remaining lenses are still worth having.
    """
    found: list[tuple[rb.Seat, Agent]] = []
    for seat in seats:
        agent = (
            await ctx.session.execute(
                select(Agent).where(Agent.name == seat.agent, Agent.org_id == ctx.org_id, Agent.enabled.is_(True))
            )
        ).scalar_one_or_none()
        if agent is not None:
            found.append((seat, agent))
    return found


def board_for(work_order: WorkOrder, boards: Any, *, author: str | None) -> list[rb.Seat]:
    """The seats this order convenes, before checking who the org actually staffs."""
    kind = _BOARD_BY_KIND.get(str(getattr(work_order, "board", "") or ""), rb.DEFAULT_BOARD)
    return rb.resolve_board(boards, level=work_order.review_level, board_name=kind, author=author)


async def status(ctx: ToolContext, wo_id: uuid.UUID, gate: str, digest: str) -> dict[str, Any]:
    """Where this gate stands for this exact submission."""
    entries = await _entries(ctx, wo_id)
    return {
        "passed": rb.has_passed(entries, gate, digest),
        "rounds": rb.rounds_run(entries, gate),
        "same_submission": rb.last_digest(entries, gate) == digest,
        "outcome": rb.outcome(entries, gate),
        "entries": entries,
    }


async def convene(
    ctx: ToolContext,
    work_order: WorkOrder,
    *,
    gate: str,
    submission: str,
    tasks: str,
    seats: list[rb.Seat],
) -> dict[str, Any]:
    """Create one reviewer run per seat and park the author on all of them.

    Raises ``RunParked`` through the caller: every question shares this tool call's
    id, so the author resumes once, with every verdict, rather than once per seat.
    """
    from api.services.agents import questions
    from api.services.agents.runtime import RunParked

    staffed = await _seat_agents(ctx, seats)
    if not staffed:
        return {"convened": []}

    digest = rb.fingerprint(submission)
    runs = AgentRunRepository(ctx.session, ctx.org_id)
    names: list[str] = []
    for seat, agent in staffed:
        peer_run = await runs.create_run(
            agent_id=agent.id,
            provider=agent.provider,
            # Reviewing is a reading task; it does not need the author's model.
            model=agent.review_model or agent.model,
            trigger="consult",
            input={
                "task": rb.review_brief(
                    gate=gate, seat=seat, work_order=work_order.title, submission=submission, tasks=tasks
                )
            },
            parent_run_id=ctx.run_id,
            work_order_id=work_order.id,
            # The reviewer reads with the author's entitlement, never wider.
            actor_user_id=ctx.actor_user_id,
            status="queued",
            label=f"Review ({gate}): {agent.name}",
        )
        await questions.create_question(
            ctx.session,
            ctx.org_id,
            run_id=ctx.run_id,  # type: ignore[arg-type]
            tool_call_id=ctx.tool_call_id or "",
            asked_by_agent_id=ctx.agent.id if ctx.agent else None,
            question=f"Review the {gate} for “{work_order.title}” through your lens.",
            audience="agent",
            target_agent_id=agent.id,
            peer_run_id=peer_run.id,
            work_order_id=work_order.id,
        )
        names.append(agent.name)

    ctx.session.add(
        WorkOrderEntry(
            work_order_id=work_order.id,
            org_id=ctx.org_id,
            role="review",
            text=rb.convene_marker(gate, digest, [s for s, _ in staffed]),
        )
    )
    await ctx.session.flush()
    raise RunParked("consult", {"review": gate, "board": names})


async def record_verdict(ctx: ToolContext, question: Any, answer: str) -> None:
    """Write one reviewer's PASS/FAIL into the diary as it replies.

    In the diary rather than a column, so the whole review reads in the record a
    person already scrolls — and so the author's next call can read the board's
    state without either side holding it in memory.
    """
    # Read defensively: this hangs off the ordinary consult path, and an ordinary
    # consult has nothing to do with a board. Anything that is not a work-order
    # question is left exactly as it was.
    wo_id = getattr(question, "work_order_id", None)
    if wo_id is None or getattr(question, "audience", None) != "agent":
        return
    reviewer = ctx.agent.name if ctx.agent else "reviewer"
    verdict = rb.parse_verdict(answer)
    ctx.session.add(
        WorkOrderEntry(
            work_order_id=wo_id,
            org_id=ctx.org_id,
            agent_id=ctx.agent.id if ctx.agent else None,
            agent_run_id=ctx.run_id,
            role="review",
            text=rb.verdict_marker(reviewer, verdict, answer.strip()[:1500]),
        )
    )
    await ctx.session.flush()
