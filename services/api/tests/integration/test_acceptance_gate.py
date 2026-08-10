"""The auditor that asks whether the delivered work answers the request.

The failure it exists for: a person filed "Check out SEO on redarchlabs.com and tell
me what you think". Four levels of delegation restated it — audit → crawl → design a
crawler → write up the crawler design — and a three-agent adversarial review board
spent four rounds arguing about render-completeness heuristics in a design nobody had
asked for. Every reviewer judged the design on its own terms. None asked whether the
website had been looked at. Nine steps closed green.

Evidence and deliverable rules cannot catch that: something *was* produced and (had it
been attached) something *was* attached. Only a reader holding the original request can
see it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentNotification
from api.models.org import Org
from api.models.work_order import WorkOrderArtifact
from api.services.agents import acceptance
from api.services.agents.acceptance import Verdict, _closing_report, _parse, check_acceptance
from api.services.agents.work_order_service import WorkOrderService, WorkOrderValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_GOOD = "Produced the SEO crawler design document and attached it as design.md."

_REAL_BRIEF = "Check out SEO on redarchlabs.com and tell me what you think"


def _settings(**overrides: Any):
    """Real Settings with the acceptance knobs overridden.

    A hand-rolled stand-in breaks the moment a notification tries to reach SMTP —
    which every failing verdict does, because telling a person is half the point.
    """
    from api.config import get_settings

    return get_settings().model_copy(update={"agent_acceptance_enforce": True, **overrides})


async def _seed(admin_session: AsyncSession, *, title: str = _REAL_BRIEF, tasks: list[str] | None = None, **kw):
    org = Org(name=f"Accept-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    agent = Agent(name="analyst", provider="openai", model="m", kind="advisory", org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()
    svc = WorkOrderService(admin_session, org.id, _settings(**kw))
    wo = await svc.create_work_order(title=title, assigned_agent_id=agent.id)
    wo.status = "in_progress"
    await svc.set_tasks(wo.id, [{"title": t, "sort_order": i} for i, t in enumerate(tasks or ["A"])])
    await admin_session.flush()
    return org, wo, agent, svc


def _verdict(monkeypatch, verdict: Verdict) -> list[Any]:
    """Stub the auditor; return the list it records calls in."""
    calls: list[Any] = []

    async def _fake(session, org_id, wo, settings, *, model=None):
        calls.append(wo.id)
        return verdict

    monkeypatch.setattr("api.services.agents.work_order_service.check_acceptance", _fake)
    return calls


class TestReadingTheVerdict:
    def test_a_pass_is_a_pass(self) -> None:
        assert _parse("PASS").ok

    def test_a_fail_carries_the_gap(self) -> None:
        v = _parse("FAIL — asked for an SEO review of the live site; got a crawler design document.")

        assert v.ok is False
        assert "crawler design document" in v.gap

    def test_a_bare_fail_still_says_something(self) -> None:
        assert _parse("FAIL").gap

    def test_a_non_answer_does_not_block(self) -> None:
        """No verdict is the auditor failing to work, not the work failing. Blocking
        on a model that rambled would freeze orders for a reason nobody can act on."""
        v = _parse("I would need more information about the deliverable to judge this.")

        assert v.ok is True
        assert v.checked is False

    def test_reasoning_before_the_verdict_is_still_read(self) -> None:
        v = _parse("Looking at the request and the artifact:\nFAIL — the site was never opened.")

        assert v.ok is False


class TestWhatTheAuditorIsShown:
    async def test_the_platforms_own_notices_are_not_the_deliverable(self, admin_session: AsyncSession) -> None:
        """A diary full of blocked/done/capability markers reads as activity, which is
        exactly the illusion this check exists to see through."""
        org, wo, agent, svc = await _seed(admin_session)
        await svc.add_entry(wo.id, text="⛔ Blocked: T2 — something", role="system")
        await svc.add_entry(wo.id, text="✅ Done: T1 — something else", role="system")
        await svc.add_entry(wo.id, text="Here are the SEO findings for the site.", role="analyst")

        report = await _closing_report(admin_session, org.id, wo.id)

        assert "SEO findings" in report
        assert "Blocked" not in report and "Done:" not in report

    async def test_it_runs_without_a_key_and_says_it_did_not_check(
        self, admin_session: AsyncSession, monkeypatch
    ) -> None:
        # Fail open: an auditor that cannot run must not freeze every order there is.
        org, wo, agent, svc = await _seed(admin_session)

        async def _no_key(*a, **kw):
            return None

        monkeypatch.setattr("api.services.agents.acceptance.resolve_provider_key", _no_key)

        verdict = await check_acceptance(admin_session, org.id, wo, _settings())

        assert verdict.ok is True
        assert verdict.checked is False


class TestTheGate:
    async def test_a_failed_verdict_stops_the_order_closing(self, admin_session: AsyncSession, monkeypatch) -> None:
        # The whole point, replayed: the last step will not close on a deliverable
        # that answers a different question.
        org, wo, agent, svc = await _seed(admin_session)
        _verdict(monkeypatch, Verdict(ok=False, gap="asked for a review of the live site; got a crawler design."))

        with pytest.raises(WorkOrderValidationError) as exc:
            await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert "does not answer the request" in str(exc.value)
        assert (await svc.list_tasks(wo.id))[0].status == "pending"

    async def test_a_failed_verdict_tells_a_person_what_is_missing(
        self, admin_session: AsyncSession, monkeypatch
    ) -> None:
        org, wo, agent, svc = await _seed(admin_session)
        _verdict(monkeypatch, Verdict(ok=False, gap="the website was never opened."))

        with pytest.raises(WorkOrderValidationError):
            await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        alerts = (
            (await admin_session.execute(select(AgentNotification).where(AgentNotification.org_id == org.id)))
            .scalars()
            .all()
        )
        assert [a.kind for a in alerts] == ["escalation"]
        assert "never opened" in (alerts[0].body or "")
        assert any("🎯" in e.text for e in await svc.list_entries(wo.id))

    async def test_a_pass_lets_it_close(self, admin_session: AsyncSession, monkeypatch) -> None:
        org, wo, agent, svc = await _seed(admin_session)
        _verdict(monkeypatch, Verdict(ok=True))

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert (await svc.list_tasks(wo.id))[0].status == "done"

    async def test_only_the_closing_step_is_audited(self, admin_session: AsyncSession, monkeypatch) -> None:
        """One call per order, not per step. Auditing a half-finished order judges a
        delivery that does not exist yet, and bills for the privilege."""
        org, wo, agent, svc = await _seed(admin_session, tasks=["A", "B", "C"])
        calls = _verdict(monkeypatch, Verdict(ok=True))

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)
        await svc.update_task_status(wo.id, "T2", "done", agent=agent, evidence=_GOOD)

        assert calls == []

        await svc.update_task_status(wo.id, "T3", "done", agent=agent, evidence=_GOOD)

        assert calls == [wo.id]

    async def test_a_blocked_step_is_never_audited(self, admin_session: AsyncSession, monkeypatch) -> None:
        org, wo, agent, svc = await _seed(admin_session)
        calls = _verdict(monkeypatch, Verdict(ok=False, gap="nope"))

        await svc.update_task_status(wo.id, "T1", "blocked", agent=agent)

        assert calls == []

    async def test_report_only_mode_records_without_blocking(self, admin_session: AsyncSession, monkeypatch) -> None:
        """How you try the auditor on a live org: it tells a person, and gets out of
        the way. A check nobody can switch off gets switched off by deleting it."""
        org, wo, agent, svc = await _seed(admin_session, agent_acceptance_enforce=False)
        _verdict(monkeypatch, Verdict(ok=False, gap="the website was never opened."))

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        assert (await svc.list_tasks(wo.id))[0].status == "done"
        assert any("🎯" in e.text for e in await svc.list_entries(wo.id))

    async def test_an_unchecked_verdict_does_not_block_or_alarm(self, admin_session: AsyncSession, monkeypatch) -> None:
        org, wo, agent, svc = await _seed(admin_session)
        _verdict(monkeypatch, Verdict(ok=True, checked=False))

        await svc.update_task_status(wo.id, "T1", "done", agent=agent, evidence=_GOOD)

        alerts = (
            (await admin_session.execute(select(AgentNotification).where(AgentNotification.org_id == org.id)))
            .scalars()
            .all()
        )
        assert alerts == []

    async def test_the_gate_is_skipped_when_the_service_has_no_settings(
        self, admin_session: AsyncSession, monkeypatch
    ) -> None:
        """Plenty of internal callers construct the service without settings; they
        must not start making model calls as a side effect."""
        org = Org(name=f"Accept-{uuid.uuid4().hex[:8]}", permission_number=1)
        admin_session.add(org)
        await admin_session.flush()
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(title=_REAL_BRIEF)
        await svc.set_tasks(wo.id, [{"title": "A", "sort_order": 0}])
        calls = _verdict(monkeypatch, Verdict(ok=False, gap="nope"))

        await svc.update_task_status(wo.id, "T1", "done", evidence=_GOOD)

        assert calls == []


class TestTheOrderThatFailed:
    async def test_the_real_case_is_refused(self, admin_session: AsyncSession, monkeypatch) -> None:
        """End to end on the actual data: the brief asked for an opinion on a live
        site, the delivery was a crawler design, and every earlier check passes it."""
        org, wo, agent, svc = await _seed(admin_session, title=_REAL_BRIEF, tasks=["Design the crawler"])
        admin_session.add(
            WorkOrderArtifact(work_order_id=wo.id, kind="output", filename="crawler-design.md", org_id=org.id)
        )
        await admin_session.flush()
        _verdict(
            monkeypatch,
            Verdict(ok=False, gap="asked for an opinion on the live site; delivered a crawler design document."),
        )

        with pytest.raises(WorkOrderValidationError) as exc:
            await svc.update_task_status(
                wo.id,
                "T1",
                "done",
                agent=agent,
                evidence="Wrote the headless rendering strategy and attached crawler-design.md.",
            )

        # Evidence was real and an artifact was attached — only the auditor catches it.
        assert "crawler design document" in str(exc.value)


def test_the_auditor_is_told_to_fail_a_methodology() -> None:
    """The prompt has to name the drift explicitly. A general "does this answer the
    request" instruction rates a thorough design document highly, because it is one."""
    assert "methodology" in acceptance._SYSTEM
    assert "here is how one would do it" in acceptance._SYSTEM
