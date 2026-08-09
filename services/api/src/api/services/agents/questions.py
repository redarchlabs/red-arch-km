"""Open questions: asking, answering, and resuming the run that was blocked.

The approval path (``approvals.py``) resumes a parked run by *permitting* a tool it
had already chosen. This path resumes it by *supplying a result* the agent could not
compute itself — a human's typed answer, or a peer agent's advice.

Both ride the same parking machinery, and the difference is one key in the run's
resume state. An approval adds the tool name to ``resume["approved"]`` and the loop
re-executes the call; an answer adds ``resume["answers"][tool_call_id]`` and the loop
**skips execution entirely**, feeding the stored payload back as that call's output.
That distinction matters: re-executing ``ask_human`` would just ask again forever.

Every terminal transition of every run funnels through :mod:`lifecycle`, which calls
:func:`settle_for_peer_run` and :func:`void_open_questions` from here. Without those
two hooks a question is a way to hang a run permanently: the peer could finish
without answering, or the asker could be cancelled while a human still holds an open
question whose answer would then re-queue a dead run.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentQuestion, AgentRun
from api.repositories.agent_questions import AgentQuestionRepository
from api.repositories.agent_run import AgentRunRepository

logger = logging.getLogger(__name__)


class QuestionError(Exception):
    pass


class QuestionNotFoundError(QuestionError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AnswerOutcome:
    """What happened to the asking run when the answer landed.

    ``resumed`` False means the answer was recorded but nothing was re-queued —
    the asking run had already ended. The caller surfaces that rather than
    implying the agent is now acting on the answer.
    """

    question: AgentQuestion
    resumed: bool


async def create_question(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    tool_call_id: str,
    asked_by_agent_id: uuid.UUID | None,
    question: str,
    audience: str = "human",
    context: str | None = None,
    target_agent_id: uuid.UUID | None = None,
    peer_run_id: uuid.UUID | None = None,
    work_order_id: uuid.UUID | None = None,
) -> AgentQuestion:
    row = AgentQuestion(
        run_id=run_id,
        tool_call_id=tool_call_id,
        asked_by_agent_id=asked_by_agent_id,
        audience=audience,
        target_agent_id=target_agent_id,
        peer_run_id=peer_run_id,
        work_order_id=work_order_id,
        question=question,
        context=context,
        status="pending",
        org_id=org_id,
    )
    session.add(row)
    await session.flush()
    return row


async def _siblings(session: AsyncSession, org_id: uuid.UUID, question: AgentQuestion) -> list[AgentQuestion]:
    """The other questions asked by the same tool call, if any.

    Empty for every ordinary consult — one call, one question — so this costs a
    single indexed lookup and changes nothing outside a board.
    """
    rows = (
        (
            await session.execute(
                select(AgentQuestion).where(
                    AgentQuestion.run_id == question.run_id,
                    AgentQuestion.tool_call_id == question.tool_call_id,
                    AgentQuestion.org_id == org_id,
                    AgentQuestion.id != question.id,
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _combined(question: AgentQuestion, siblings: list[AgentQuestion], payload: dict[str, Any]) -> dict[str, Any]:
    """One result carrying every reviewer's answer, in the order they were asked."""
    everyone = sorted([question, *siblings], key=lambda q: q.created_at)
    return {
        "answers": [
            {
                # Who was ASKED, not who replied: the reply comes from the run that
                # was spawned for it, and the asker cares which seat spoke.
                "agent_id": str(q.target_agent_id) if q.target_agent_id else None,
                "answer": payload.get("answer") if q.id == question.id else q.answer,
                "declined": q.status == "declined",
            }
            for q in everyone
        ],
        # Several agents answered one tool call, which only a review board does.
        # Resuming a parked call feeds the answer in *instead of* running the
        # handler, so the tool that convened the board never sees these verdicts —
        # and a model handed a wall of review notes with no instruction reports
        # "submitted" and stops, leaving the plan neither approved nor rejected.
        # Observed on the first live board; the note is what closes that loop.
        "note": (
            "Every reviewer has now answered. Call the same tool again with your "
            "submission to continue — that is what records their verdicts and moves "
            "this forward. Address any objection first; resubmitting unchanged text "
            "will not re-open the review."
        ),
    }


async def _inject_answer(
    session: AsyncSession,
    org_id: uuid.UUID,
    question: AgentQuestion,
    payload: dict[str, Any],
) -> bool:
    """Hand ``payload`` to the parked tool call and re-queue the asking run.

    Returns whether the run was actually resumed. A run that is no longer
    ``waiting`` (cancelled, timed out, finalized by another path) is left alone —
    re-queueing it would restart work its owner already concluded.
    """
    run = await AgentRunRepository(session, org_id).get_run(question.run_id)
    if run is None or run.status != "waiting":
        return False

    # A review board asks several agents on ONE tool call, so several questions
    # share a tool_call_id. The parked call has one result to receive, and the
    # board's answer is all of them — resuming on the first would hand the author
    # one reviewer's verdict and discard the rest.
    siblings = await _siblings(session, org_id, question)
    if siblings:
        if any(s.status == "pending" for s in siblings):
            return False
        payload = _combined(question, siblings, payload)

    run_input = dict(run.input or {})
    resume = dict(run_input.get("resume") or {"messages": [], "pending": [], "approved": []})
    answers = dict(resume.get("answers") or {})
    answers[question.tool_call_id] = payload
    resume["answers"] = answers
    run_input["resume"] = resume
    # Reassigned rather than mutated in place: JSONB columns are compared by
    # identity for change detection, so an in-place mutation is not flushed.
    run.input = run_input
    run.status = "queued"  # the worker sweep picks it up and continues the turn
    run.wait_kind = None
    run.last_activity_at = _now()
    await session.flush()
    return True


async def record_answer(
    session: AsyncSession,
    org_id: uuid.UUID,
    question: AgentQuestion,
    *,
    answer: str,
    by_profile_id: uuid.UUID | None = None,
    by_agent_id: uuid.UUID | None = None,
    answered_by: str | None = None,
) -> AnswerOutcome:
    """Answer an open question and resume the asker."""
    if question.status != "pending":
        raise QuestionError("That question has already been settled")

    question.status = "answered"
    question.answer = answer
    question.answered_by_profile_id = by_profile_id
    question.answered_by_agent_id = by_agent_id
    question.answered_at = _now()

    resumed = await _inject_answer(
        session,
        org_id,
        question,
        {"answered_by": answered_by or ("an agent" if by_agent_id else "a human"), "answer": answer},
    )
    if not resumed:
        # Recorded, not delivered. Keeping the answer is still worth it — it is the
        # audit trail of what a human said — but the status must not read "answered
        # and acted on".
        question.status = "voided"
        await session.flush()
    return AnswerOutcome(question=question, resumed=resumed)


async def decline(
    session: AsyncSession,
    org_id: uuid.UUID,
    question: AgentQuestion,
    *,
    reason: str | None = None,
    by_profile_id: uuid.UUID | None = None,
) -> AnswerOutcome:
    """Refuse to answer, but still unblock the run.

    The agent is told plainly that no answer is coming so it can proceed on its own
    judgement or escalate. Dropping the question instead would leave the run parked
    until the escalation backstop noticed it hours later.
    """
    if question.status != "pending":
        raise QuestionError("That question has already been settled")

    question.status = "declined"
    question.answer = reason
    question.answered_by_profile_id = by_profile_id
    question.answered_at = _now()
    note = reason or "No answer is available."
    resumed = await _inject_answer(
        session,
        org_id,
        question,
        {"answered": False, "answer": note, "guidance": "Proceed on your own judgement or escalate."},
    )
    await session.flush()
    return AnswerOutcome(question=question, resumed=resumed)


async def answer_question(
    session: AsyncSession,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
    *,
    answer: str,
    by_profile_id: uuid.UUID | None = None,
) -> AnswerOutcome:
    """Human-facing entry point (the inbox route)."""
    question = await _get_human_question(session, org_id, question_id)
    return await record_answer(session, org_id, question, answer=answer, by_profile_id=by_profile_id)


async def decline_question(
    session: AsyncSession,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
    *,
    reason: str | None = None,
    by_profile_id: uuid.UUID | None = None,
) -> AnswerOutcome:
    question = await _get_human_question(session, org_id, question_id)
    return await decline(session, org_id, question, reason=reason, by_profile_id=by_profile_id)


async def _get_human_question(session: AsyncSession, org_id: uuid.UUID, question_id: uuid.UUID) -> AgentQuestion:
    """Fetch a question a *person* is entitled to settle.

    The audience check is the point. ``list_pending(audience="human")`` filters the
    inbox, but the fetch-by-id did not — so a human holding a consult's id could
    answer a question addressed to an *agent*. The consulted agent would then find
    nothing waiting on it (``reply_to_peer`` returns "No one is waiting on you")
    and its entire run would be discarded, while the asker resumed on an answer
    from someone who was never asked.
    """
    question = await AgentQuestionRepository(session, org_id).get(question_id)
    if question is None:
        raise QuestionNotFoundError(f"Question {question_id} not found")
    if question.audience != "human":
        # A 404 rather than a 403: the id of another agent's consult is not a thing
        # this endpoint has, and saying "exists but not yours" only confirms it.
        raise QuestionNotFoundError(f"Question {question_id} not found")
    return question


# --- lifecycle hooks -------------------------------------------------------


async def settle_for_peer_run(session: AsyncSession, org_id: uuid.UUID, run: AgentRun, *, reason: str) -> None:
    """A consult run ended — make sure it didn't leave its asker parked forever.

    Called from every terminal transition. If the peer answered, its question is
    already ``answered`` and this is a no-op; if it finished, errored, or was
    cancelled without calling ``reply_to_peer``, the asker resumes with an explicit
    "no answer" rather than waiting on a run that will never speak again.
    """
    question = await AgentQuestionRepository(session, org_id).pending_for_peer_run(run.id)
    if question is None:
        return
    logger.info("consult run %s ended without answering question %s", run.id, question.id)
    await decline(session, org_id, question, reason=reason)


async def void_open_questions(session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """The asking run ended — retire its open questions.

    A pending question against a finished run is a live trap: answering it would
    try to re-queue a run whose owner already decided its fate.
    """
    await session.execute(
        update(AgentQuestion)
        .where(
            AgentQuestion.run_id == run_id,
            AgentQuestion.org_id == org_id,
            AgentQuestion.status == "pending",
        )
        .values(status="voided", answered_at=_now())
    )
    # Any consult run spawned for one of those questions is now pointless work.
    await _cancel_orphaned_consults(session, org_id, run_id)


async def _cancel_orphaned_consults(session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Stop consult runs whose asker is gone (they burn tokens for nobody)."""
    peer_run_ids = (
        (
            await session.execute(
                select(AgentQuestion.peer_run_id).where(
                    AgentQuestion.run_id == run_id,
                    AgentQuestion.org_id == org_id,
                    AgentQuestion.peer_run_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not peer_run_ids:
        return
    ids = list(peer_run_ids)
    await session.execute(
        update(AgentRun)
        .where(
            AgentRun.id.in_(ids),
            AgentRun.org_id == org_id,
            AgentRun.status.in_(("queued", "waiting")),
        )
        .values(status="cancelled", error="the agent that asked is no longer waiting", wait_kind=None)
    )
    # A consult run can be `waiting` because it asked a *human* something. Cancelling
    # it here bypasses lifecycle.cancel_run, so retire that question too — otherwise
    # it sits in the inbox forever, and answering it silently does nothing. One level
    # is enough: the depth cap means a consult run never has consults of its own.
    await session.execute(
        update(AgentQuestion)
        .where(
            AgentQuestion.run_id.in_(ids),
            AgentQuestion.org_id == org_id,
            AgentQuestion.status == "pending",
        )
        .values(status="voided", answered_at=_now())
    )
