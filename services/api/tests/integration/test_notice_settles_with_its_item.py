"""A notice stops asking once its item is settled.

An approval or question notification says "go and do this". After the decision is
made it is a receipt — but it stayed ``unread``, so the inbox listed it forever with
a "Resolve" button whose only effect was ticking off work already done. Seen live:
eleven open rows, eight of them for items settled hours earlier. A chore list that is
mostly finished is one people stop reading, which defeats the point of having it.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentApproval, AgentNotification, AgentQuestion, AgentRun
from api.models.org import Org
from api.services.agents import questions as question_service
from api.services.agents.approvals import ApprovalService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession) -> tuple[Org, AgentRun]:
    org = Org(name=f"Notice-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="op", provider="openai", model="m", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    run = AgentRun(
        agent_id=agent.id,
        provider="openai",
        model="m",
        status="waiting",
        wait_kind="approval",
        input={"resume": {"messages": [], "pending": [], "approved": []}},
        org_id=org.id,
    )
    admin_session.add(run)
    await admin_session.flush()
    return org, run


async def _notice(admin_session: AsyncSession, org: Org, run: AgentRun, kind: str) -> AgentNotification:
    row = AgentNotification(
        kind=kind, title=f"{kind} notice", run_id=run.id, status="unread", delivered_channels=["in_app"], org_id=org.id
    )
    admin_session.add(row)
    await admin_session.flush()
    return row


async def _statuses(admin_session: AsyncSession, org: Org) -> list[str]:
    rows = await admin_session.execute(select(AgentNotification.status).where(AgentNotification.org_id == org.id))
    return [r[0] for r in rows.all()]


class TestApprovalNotices:
    async def test_approving_closes_the_notice(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        approval = AgentApproval(run_id=run.id, tool_name="run_workflow", arguments={}, status="pending", org_id=org.id)
        admin_session.add(approval)
        await admin_session.flush()
        await _notice(admin_session, org, run, "approval")

        await ApprovalService(admin_session, org.id).approve(approval.id, None)

        assert await _statuses(admin_session, org) == ["resolved"]

    async def test_denying_closes_it_too(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        approval = AgentApproval(run_id=run.id, tool_name="run_workflow", arguments={}, status="pending", org_id=org.id)
        admin_session.add(approval)
        await admin_session.flush()
        await _notice(admin_session, org, run, "approval")

        await ApprovalService(admin_session, org.id).deny(approval.id, None)

        assert await _statuses(admin_session, org) == ["resolved"]

    async def test_a_second_pending_approval_keeps_the_notice_open(self, admin_session: AsyncSession) -> None:
        """A run may raise another ask while the first is being decided. Closing the
        notice then would hide a real request behind a decision about a different one."""
        org, run = await _seed(admin_session)
        first = AgentApproval(run_id=run.id, tool_name="run_workflow", arguments={}, status="pending", org_id=org.id)
        second = AgentApproval(run_id=run.id, tool_name="send_email", arguments={}, status="pending", org_id=org.id)
        admin_session.add_all([first, second])
        await admin_session.flush()
        await _notice(admin_session, org, run, "approval")

        await ApprovalService(admin_session, org.id).approve(first.id, None)

        assert await _statuses(admin_session, org) == ["unread"]

    async def test_an_escalation_on_the_same_run_is_left_alone(self, admin_session: AsyncSession) -> None:
        """Escalations are not receipts — they mean work stopped, and only a person
        decides they are done."""
        org, run = await _seed(admin_session)
        approval = AgentApproval(run_id=run.id, tool_name="run_workflow", arguments={}, status="pending", org_id=org.id)
        admin_session.add(approval)
        await admin_session.flush()
        await _notice(admin_session, org, run, "escalation")

        await ApprovalService(admin_session, org.id).approve(approval.id, None)

        assert await _statuses(admin_session, org) == ["unread"]


class TestQuestionNotices:
    async def _ask(self, admin_session: AsyncSession, org: Org, run: AgentRun) -> AgentQuestion:
        question = AgentQuestion(
            run_id=run.id,
            tool_call_id="c1",
            audience="human",
            question="Which key?",
            status="pending",
            org_id=org.id,
        )
        admin_session.add(question)
        await admin_session.flush()
        return question

    async def test_answering_closes_the_notice(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        question = await self._ask(admin_session, org, run)
        await _notice(admin_session, org, run, "question")

        await question_service.record_answer(admin_session, org.id, question, answer="use this one")

        assert await _statuses(admin_session, org) == ["resolved"]

    async def test_declining_closes_it_too(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        question = await self._ask(admin_session, org, run)
        await _notice(admin_session, org, run, "question")

        await question_service.decline(admin_session, org.id, question, reason="your call")

        assert await _statuses(admin_session, org) == ["resolved"]

    async def test_a_run_that_ends_retires_its_notice(self, admin_session: AsyncSession) -> None:
        # The question is voided because nobody is listening any more, so asking a
        # person to answer it is asking them to talk to a wall.
        org, run = await _seed(admin_session)
        await self._ask(admin_session, org, run)
        await _notice(admin_session, org, run, "question")

        await question_service.void_open_questions(admin_session, org.id, run.id)

        assert await _statuses(admin_session, org) == ["resolved"]

    async def test_a_second_open_question_keeps_the_notice(self, admin_session: AsyncSession) -> None:
        org, run = await _seed(admin_session)
        first = await self._ask(admin_session, org, run)
        second = AgentQuestion(
            run_id=run.id,
            tool_call_id="c2",
            audience="human",
            question="And the budget?",
            status="pending",
            org_id=org.id,
        )
        admin_session.add(second)
        await admin_session.flush()
        await _notice(admin_session, org, run, "question")

        await question_service.record_answer(admin_session, org.id, first, answer="this one")

        assert await _statuses(admin_session, org) == ["unread"]

    async def test_a_peer_consult_does_not_close_a_human_notice(self, admin_session: AsyncSession) -> None:
        """An agent-audience question left pending is not something a person owes an
        answer to, so it must not hold the human notice open — nor close it."""
        org, run = await _seed(admin_session)
        human = await self._ask(admin_session, org, run)
        admin_session.add(
            AgentQuestion(
                run_id=run.id,
                tool_call_id="c2",
                audience="agent",
                question="thoughts?",
                status="pending",
                org_id=org.id,
            )
        )
        await admin_session.flush()
        await _notice(admin_session, org, run, "question")

        await question_service.record_answer(admin_session, org.id, human, answer="done")

        assert await _statuses(admin_session, org) == ["resolved"]
