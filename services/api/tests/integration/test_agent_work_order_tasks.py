"""An agent can plan a work order and report against the plan.

Before these tools an agent could describe its intentions in the diary, but
nothing it wrote became the order's *task list* — that could only be set through
the API by a person. So a coordinator handed a job had no way to break it down
where anyone could see the breakdown, and an order that fanned out to five
delegations sat at 0% complete forever.

The tools act on the work order the run already belongs to, never on an id the
model supplies: an agent rewriting another order's plan is not a capability
anyone asked for, and taking the id from the run makes it impossible rather than
merely discouraged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.models.org import Org
from api.services.agents.tools.work_order_tasks import (
    LIST_WORK_ORDER_TASKS,
    SET_WORK_ORDER_TASKS,
    UPDATE_WORK_ORDER_TASK,
)
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@dataclass
class _Ctx:
    """Only the ToolContext fields these handlers read."""

    session: Any
    org_id: uuid.UUID
    work_order_id: uuid.UUID | None
    agent: Any = None
    run_id: uuid.UUID | None = None
    settings: Any = None
    actor_user_id: uuid.UUID | None = None
    tool_call_id: str | None = None
    _extra: dict = field(default_factory=dict)


async def _seed(admin_session: AsyncSession) -> tuple[Org, uuid.UUID]:
    org = Org(name=f"Tasks-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    wo = await WorkOrderService(admin_session, org.id).create_work_order(title="Audit the site")
    await admin_session.commit()
    return org, wo.id


class TestPlanning:
    async def test_an_agent_can_write_the_plan(self, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)

        out = await SET_WORK_ORDER_TASKS.handler(
            ctx, {"tasks": ["Crawl the site", "Check meta tags", "Report findings"]}
        )

        assert [t["title"] for t in out["tasks"]] == ["Crawl the site", "Check meta tags", "Report findings"]
        # Keys are how the agent addresses a step afterwards.
        assert [t["key"] for t in out["tasks"]] == ["T1", "T2", "T3"]

    async def test_setting_a_plan_replaces_the_old_one(self, admin_session: AsyncSession) -> None:
        """A plan states the work as now understood. Merging would silently keep
        steps the agent has just decided against."""
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)
        await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["Old one", "Old two"]})

        out = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["Only this"]})

        assert [t["title"] for t in out["tasks"]] == ["Only this"]

    async def test_a_blank_plan_is_refused(self, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)

        assert "error" in await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": []})
        assert "error" in await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["  ", ""]})

    async def test_an_absurd_plan_is_refused(self, admin_session: AsyncSession) -> None:
        """One bad turn should not write hundreds of rows."""
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)

        out = await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": [f"step {i}" for i in range(200)]})

        assert "error" in out


class TestTracking:
    async def test_marking_a_step_moves_the_progress_figure(self, admin_session: AsyncSession) -> None:
        # Percent complete comes from these updates; an unupdated list reads as no
        # progress however much work was actually done.
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)
        await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["One", "Two"]})

        out = await UPDATE_WORK_ORDER_TASK.handler(ctx, {"key": "T1", "status": "done"})

        assert out["updated"]["status"] == "done"
        assert out["progress"] == 0.5
        assert out["remaining"] == ["Two"]

    async def test_an_unknown_key_names_the_real_ones(self, admin_session: AsyncSession) -> None:
        """A model that guessed a key has no other way to find the right one, and
        would otherwise abandon the update — the same dead end as an unknown peer."""
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)
        await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["One", "Two"]})

        out = await UPDATE_WORK_ORDER_TASK.handler(ctx, {"key": "step-1", "status": "done"})

        assert "T1" in out["error"] and "T2" in out["error"]

    async def test_an_invalid_status_is_refused_with_the_options(self, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)
        await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["One"]})

        out = await UPDATE_WORK_ORDER_TASK.handler(ctx, {"key": "T1", "status": "finished"})

        assert "blocked" in out["error"]

    async def test_reading_the_plan_back(self, admin_session: AsyncSession) -> None:
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)
        await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["One", "Two"]})
        await UPDATE_WORK_ORDER_TASK.handler(ctx, {"key": "T2", "status": "blocked"})

        out = await LIST_WORK_ORDER_TASKS.handler(ctx, {})

        assert {t["key"]: t["status"] for t in out["tasks"]} == {"T1": "pending", "T2": "blocked"}

    async def test_a_carried_step_does_not_count_against_progress(self, admin_session: AsyncSession) -> None:
        """`carried` means deliberately not doing it here, so it must not make an
        otherwise finished order look incomplete."""
        org, wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=wo_id)
        await SET_WORK_ORDER_TASKS.handler(ctx, {"tasks": ["One", "Two"]})
        await UPDATE_WORK_ORDER_TASK.handler(ctx, {"key": "T1", "status": "done"})

        out = await UPDATE_WORK_ORDER_TASK.handler(ctx, {"key": "T2", "status": "carried"})

        assert out["progress"] == 1.0


class TestScope:
    async def test_a_run_with_no_work_order_is_told_why(self, admin_session: AsyncSession) -> None:
        """A console chat or a scheduled run has no order. Failing silently would
        look to the model like the plan was saved."""
        org, _wo_id = await _seed(admin_session)
        ctx = _Ctx(session=admin_session, org_id=org.id, work_order_id=None)

        for tool, args in (
            (SET_WORK_ORDER_TASKS, {"tasks": ["One"]}),
            (UPDATE_WORK_ORDER_TASK, {"key": "T1", "status": "done"}),
            (LIST_WORK_ORDER_TASKS, {}),
        ):
            out = await tool.handler(ctx, args)
            assert "not attached to a work order" in out["error"]
