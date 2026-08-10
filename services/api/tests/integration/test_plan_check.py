"""The check that a plan is a plan this org can actually carry out.

The failure it exists for, recorded from a live run. A person filed "Check out SEO on
redarchlabs.com and tell me what you think". The research analyst — holding one tool
that fetches one page, and a search tool with no key — planned a 500-page crawl, a
Lighthouse pass, five screenshots and a backlink summary. It closed step one in two
calls and then spent six runs failing to start step two, because none of those
capabilities exist anywhere in that org and never did.

Every component behaved correctly. The stall sweeper even stopped picking the order
up, exactly as designed, once continuations stopped closing steps. The plan was for a
different organisation, and until this nothing had an opinion about that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.models.agent import Agent
from api.models.org import Org
from api.services.agents.plan_check import PlanVerdict, _parse, check_plan, org_toolbox
from api.services.agents.tools.work_order_tasks import SET_WORK_ORDER_TASKS
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

# The plan as the analyst actually wrote it, trimmed to the steps that could not be done.
_IMPOSSIBLE = [
    "T1: Fetch and inspect robots.txt and sitemap(s)",
    "T2: Crawl up to 500 public pages (depth 3, <=2 req/s), produce CSV",
    "T3: Run Lighthouse for 10 pages and capture metrics",
]


@dataclass
class _Ctx:
    session: Any
    org_id: uuid.UUID
    work_order_id: uuid.UUID | None
    settings: Any
    agent: Any = None
    run_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    tool_call_id: str | None = None
    extras: dict = field(default_factory=dict)


def _settings():
    from api.config import get_settings

    return get_settings()


async def _seed(admin_session: AsyncSession, *, title: str = "Check out SEO and tell me what you think"):
    org = Org(name=f"Plan-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    wo = await WorkOrderService(admin_session, org.id).create_work_order(title=title)
    await admin_session.flush()
    return org, wo.id


def _judge(monkeypatch, verdict: PlanVerdict) -> list[list[str]]:
    """Stub the judge; return the list of plans it was shown."""
    seen: list[list[str]] = []

    async def _fake(session, org_id, *, brief, titles, settings, model=None):
        seen.append(list(titles))
        return verdict

    monkeypatch.setattr("api.services.agents.plan_check.check_plan", _fake)
    return seen


class TestReadingTheVerdict:
    def test_ok_is_ok(self) -> None:
        assert _parse("OK").ok

    def test_rework_carries_the_reason(self) -> None:
        v = _parse("REWORK — steps 2 and 3 need a crawler and Lighthouse; neither exists here")
        assert not v.ok
        assert "crawler" in v.problem

    def test_a_rework_with_no_reason_still_says_something(self) -> None:
        assert _parse("REWORK").problem

    def test_a_non_answer_is_not_a_rejection(self) -> None:
        # Fails open: a judge that did not answer must not stop anyone planning.
        v = _parse("I'm not sure what you mean.")
        assert v.ok and not v.checked

    def test_a_verdict_after_reasoning_is_still_read(self) -> None:
        assert not _parse("Looking at the tools listed...\nREWORK — no crawler").ok


class TestWhatTheJudgeIsShown:
    async def test_the_toolbox_is_every_tool_anyone_holds(self, admin_session: AsyncSession) -> None:
        # A planner may write steps for its reports, so its own grants are the wrong
        # question. What it may not do is plan for tools nobody has.
        org, _ = await _seed(admin_session)
        admin_session.add(
            Agent(
                name="analyst",
                provider="openai",
                model="m",
                kind="advisory",
                org_id=org.id,
                grants={"tools": ["fetch_web_page"]},
            )
        )
        admin_session.add(
            Agent(
                name="engineer",
                provider="openai",
                model="m",
                kind="operator",
                org_id=org.id,
                grants={"tools": ["create_record"]},
            )
        )
        await admin_session.flush()

        lines = await org_toolbox(admin_session, org.id, _settings())

        assert any(line.startswith("- fetch_web_page") for line in lines)
        assert any(line.startswith("- create_record") for line in lines)
        # No crawler, no Lighthouse, no screenshots — which is the point.
        assert not any("lighthouse" in line.lower() for line in lines)

    async def test_a_disabled_agents_tools_are_not_available(self, admin_session: AsyncSession) -> None:
        org, _ = await _seed(admin_session)
        admin_session.add(
            Agent(
                name="retired",
                provider="openai",
                model="m",
                kind="operator",
                org_id=org.id,
                enabled=False,
                grants={"tools": ["run_claude_code"]},
            )
        )
        await admin_session.flush()

        lines = await org_toolbox(admin_session, org.id, _settings())

        assert not any(line.startswith("- run_claude_code") for line in lines)

    async def test_read_tools_everyone_gets_are_included(self, admin_session: AsyncSession) -> None:
        # Granted to nobody explicitly, available to all — a plan built on them is fine.
        org, _ = await _seed(admin_session)
        lines = await org_toolbox(admin_session, org.id, _settings())

        assert lines

    async def test_no_key_means_no_opinion(self, monkeypatch, admin_session: AsyncSession) -> None:
        org, _ = await _seed(admin_session)

        async def _no_key(session, org_id, provider, settings):
            return None

        monkeypatch.setattr("api.services.agents.plan_check.resolve_provider_key", _no_key)

        verdict = await check_plan(admin_session, org.id, brief="anything", titles=_IMPOSSIBLE, settings=_settings())

        # Skipped, and saying so — a skip must never read as approval.
        assert verdict.ok and not verdict.checked


class TestSendingAPlanBack:
    async def test_an_unworkable_plan_is_not_saved(self, monkeypatch, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        _judge(monkeypatch, PlanVerdict(ok=False, problem="steps 2-3 need a crawler and Lighthouse"))
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id, settings=_settings())

        out = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": _IMPOSSIBLE})

        assert "crawler" in out["error"]
        # Nothing written: the agent must not end up working a plan that was refused.
        assert await WorkOrderService(admin_session, org.id).list_tasks(wo_id) == []

    async def test_the_refusal_says_what_to_do_instead(self, monkeypatch, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        _judge(monkeypatch, PlanVerdict(ok=False, problem="no crawler here"))
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id, settings=_settings())

        out = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": _IMPOSSIBLE})

        # An error the model cannot act on is a stall with extra steps.
        assert "set_work_order_tasks again" in out["error"]

    async def test_a_workable_plan_goes_straight_through(self, monkeypatch, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        _judge(monkeypatch, PlanVerdict(ok=True))
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id, settings=_settings())

        out = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["Read the home page", "Say what I think"]})

        assert [t["title"] for t in out["tasks"]] == ["Read the home page", "Say what I think"]

    async def test_a_plan_is_only_sent_back_once(self, monkeypatch, admin_session: AsyncSession) -> None:
        # A planner trapped in rework cannot plan at all, which is worse than the bad
        # plan — the acceptance gate still gets its turn at the end.
        org, wo_id = await _seed(admin_session)
        _judge(monkeypatch, PlanVerdict(ok=False, problem="still no crawler"))
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id, settings=_settings())

        first = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": _IMPOSSIBLE})
        second = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": _IMPOSSIBLE})

        assert "error" in first
        assert "tasks" in second

    async def test_the_judge_never_runs_without_settings(self, monkeypatch, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        seen = _judge(monkeypatch, PlanVerdict(ok=False, problem="no"))
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id, settings=None)

        out = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["Do the thing"]})

        assert seen == []
        assert "tasks" in out
