"""Say at dispatch what this chain of agents cannot do.

The case this comes from: "Check out SEO on redarchlabs.com" was assigned to a
coordinator, which delegated to an advisory researcher whose grants listed tool
names from a different runtime — so nothing it could reach was able to open a web
page. The order ran for five hours across four rounds of questions and ended with
every step blocked. Every fact needed to say so was in the database at the moment
it was dispatched.
"""

from __future__ import annotations

import uuid

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentNotification
from api.models.org import Org
from api.services.agents.capability import (
    capability_warnings,
    reachable_agents,
    wants_action,
    wants_web,
)
from api.services.agents.work_order_service import WorkOrderService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_WEB = ["web_research"]
_ACT = ["create_record"]


async def _org(admin_session: AsyncSession) -> Org:
    org = Org(name=f"Cap-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.flush()
    return org


async def _agent(
    admin_session: AsyncSession,
    org: Org,
    name: str,
    kind: str,
    *,
    tools: list[str] | None = None,
    supervisor: Agent | None = None,
    records_write: bool = True,
) -> Agent:
    agent = Agent(
        name=name,
        provider="openai",
        model="m",
        kind=kind,
        org_id=org.id,
        grants={"tools": tools or [], "records_write": records_write},
        supervisor_id=supervisor.id if supervisor else None,
    )
    admin_session.add(agent)
    await admin_session.flush()
    return agent


class TestWhatCountsAsWebWork:
    @pytest.mark.parametrize(
        "brief",
        [
            "Check out SEO on redarchlabs.com and tell me what you think",
            "Audit https://example.org for broken links",
            "Who are our competitors and how do they rank?",
            "Our landing page feels slow",
        ],
    )
    def test_it_recognises_the_live_web(self, brief: str) -> None:
        assert wants_web(brief)

    @pytest.mark.parametrize(
        "brief",
        [
            "Summarise the Q3 handbook for new hires",
            "Research which of our policies mention parental leave",
            "Draft a reply to the finance team",
        ],
    )
    def test_ordinary_work_is_not_web_work(self, brief: str) -> None:
        # A warning that fires on "research" is a warning people learn to ignore.
        assert not wants_web(brief)


class TestWhoTheChainCanReach:
    async def test_a_coordinator_reaches_its_reports_reports(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        ops = await _agent(admin_session, org, "operations-officer", "coordinator", supervisor=chief)
        hand = await _agent(admin_session, org, "backend-engineer", "operator", supervisor=ops)

        workers, _ = await reachable_agents(admin_session, org.id, chief)

        assert {a.name for a in workers} == {chief.name, ops.name, hand.name}

    async def test_an_advisory_agent_is_a_leaf(self, admin_session: AsyncSession) -> None:
        # The exact hole in the real roster: seo-specialist sat under an advisory
        # marketing-lead, and delegate_task is barred to advisory agents — so it
        # was drawn on the org chart and unreachable in the runtime.
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        marketing = await _agent(admin_session, org, "marketing-lead", "advisory", supervisor=chief)
        await _agent(admin_session, org, "seo-specialist", "advisory", tools=_WEB, supervisor=marketing)

        workers, _ = await reachable_agents(admin_session, org.id, chief)

        assert "seo-specialist" not in {a.name for a in workers}

    async def test_a_deep_chain_is_followed_all_the_way_down(self, admin_session: AsyncSession) -> None:
        # The runtime puts no cap on delegation depth, so neither does this. A checker
        # that stopped short would call a reachable operator unreachable and warn
        # about a gap that is not there.
        org = await _org(admin_session)
        root = await _agent(admin_session, org, "L0", "coordinator")
        parent = root
        for level in range(1, 12):
            parent = await _agent(admin_session, org, f"L{level}", "coordinator", supervisor=parent)
        await _agent(admin_session, org, "deep-operator", "operator", tools=_ACT, supervisor=parent)

        workers, _ = await reachable_agents(admin_session, org.id, root)

        assert "deep-operator" in {a.name for a in workers}
        assert await capability_warnings(admin_session, org.id, root, "Fix the pricing page") == []

    async def test_a_supervisor_cycle_does_not_hang(self, admin_session: AsyncSession) -> None:
        # Hand-edited charts can close a loop. Termination comes from having seen an
        # agent already, not from a depth counter.
        org = await _org(admin_session)
        top = await _agent(admin_session, org, "top", "coordinator")
        middle = await _agent(admin_session, org, "middle", "coordinator", supervisor=top)
        top.supervisor_id = middle.id
        await admin_session.flush()

        workers, _ = await reachable_agents(admin_session, org.id, top)

        assert {a.name for a in workers} == {"top", "middle"}

    async def test_a_disabled_report_is_not_reachable(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        gone = await _agent(admin_session, org, "backend-engineer", "operator", supervisor=chief)
        gone.enabled = False
        await admin_session.flush()

        workers, _ = await reachable_agents(admin_session, org.id, chief)

        assert {a.name for a in workers} == {chief.name}


class TestWhatCountsAsActionWork:
    @pytest.mark.parametrize(
        "brief",
        ["File the quarterly return", "Fix the broken redirect", "Send the renewal reminders"],
    )
    def test_it_recognises_a_request_to_change_something(self, brief: str) -> None:
        assert wants_action(brief)

    @pytest.mark.parametrize(
        "brief",
        ["Check out SEO on redarchlabs.com and tell me what you think", "What do our competitors rank for?"],
    )
    def test_asking_for_an_opinion_is_not_action(self, brief: str) -> None:
        assert not wants_action(brief)


class TestTheWarnings:
    async def test_web_work_with_no_web_tool_is_called_out(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "research-analyst", "advisory", supervisor=chief)

        warnings = await capability_warnings(
            admin_session, org.id, chief, "Check out SEO on redarchlabs.com and tell me what you think"
        )

        assert any("web_research" in w for w in warnings)

    async def test_a_reachable_web_tool_settles_it(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "backend-engineer", "operator", tools=_WEB + _ACT, supervisor=chief)

        warnings = await capability_warnings(admin_session, org.id, chief, "Audit https://example.org")

        assert warnings == []

    async def test_a_consultable_peer_counts_for_looking_things_up(self, admin_session: AsyncSession) -> None:
        # consult_peer reaches any advisory agent org-wide, so a peer with the tool
        # is a real answer to "can anyone open a page" — even off this branch.
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "backend-engineer", "operator", tools=_ACT, supervisor=chief)
        await _agent(admin_session, org, "seo-specialist", "advisory", tools=_WEB)

        warnings = await capability_warnings(admin_session, org.id, chief, "Audit https://example.org")

        assert warnings == []

    async def test_a_granted_web_tool_with_no_key_is_called_out(self, admin_session: AsyncSession) -> None:
        # Granted-but-keyless fails at the first call, deep inside a run, as a tool
        # error the model then reports as its own inability — which is how "no agent
        # can browse" and "browsing is misconfigured" became indistinguishable.
        from api.config import get_settings

        settings = get_settings()
        if settings.gemini_api_key.get_secret_value() or settings.anthropic_api_key.get_secret_value():
            pytest.skip("this environment has a key for one of the web backends")  # pragma: no cover
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "backend-engineer", "operator", tools=_WEB + _ACT, supervisor=chief)

        warnings = await capability_warnings(
            admin_session, org.id, chief, "Audit https://example.org", settings=settings
        )

        assert any("neither of its backends has a key" in w for w in warnings)
        # Name the free option — Google AI Studio grounding costs nothing.
        assert any("GEMINI_API_KEY is free" in w for w in warnings)

    async def test_the_keyless_route_through_the_cli_is_named(self, admin_session: AsyncSession) -> None:
        """ "Set an API key" is the wrong advice when a reachable operator can already
        reach the web on the owner's subscription. The fix is picking that agent."""
        from api.config import get_settings

        settings = get_settings().model_copy(update={"enable_claude_cli_tool": True})
        if settings.gemini_api_key.get_secret_value() or settings.anthropic_api_key.get_secret_value():
            pytest.skip("this environment has a key for one of the web backends")  # pragma: no cover
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "research-analyst", "advisory", tools=_WEB, supervisor=chief)
        await _agent(admin_session, org, "principal-engineer", "operator", tools=["run_claude_code"], supervisor=chief)

        warnings = await capability_warnings(
            admin_session, org.id, chief, "Audit https://example.org", settings=settings
        )

        # It must lead with the route that exists, not with a purchase: a warning
        # that opens "set an API key" reads as blocked-until-you-spend.
        hit = next(w for w in warnings if "principal-engineer" in w)
        assert "Send this work to principal-engineer" in hit
        assert "no key needed" in hit
        assert "GEMINI_API_KEY is free" in hit

    async def test_an_advisory_agent_is_never_offered_as_the_cli_route(self, admin_session: AsyncSession) -> None:
        """run_claude_code is EXECUTE, so the kind-gate denies it to an advisory agent
        whatever its grants say — naming one would send a person down a dead end."""
        from api.config import get_settings

        settings = get_settings().model_copy(update={"enable_claude_cli_tool": True})
        if settings.gemini_api_key.get_secret_value() or settings.anthropic_api_key.get_secret_value():
            pytest.skip("this environment has a key for one of the web backends")  # pragma: no cover
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(
            admin_session,
            org,
            "research-analyst",
            "advisory",
            tools=_WEB + ["run_claude_code"],
            supervisor=chief,
        )

        warnings = await capability_warnings(
            admin_session, org.id, chief, "Audit https://example.org", settings=settings
        )

        assert warnings
        assert not any("run_claude_code" in w for w in warnings)

    async def test_no_cli_route_is_named_when_the_tool_is_switched_off(self, admin_session: AsyncSession) -> None:
        from api.config import get_settings

        settings = get_settings().model_copy(update={"enable_claude_cli_tool": False})
        if settings.gemini_api_key.get_secret_value() or settings.anthropic_api_key.get_secret_value():
            pytest.skip("this environment has a key for one of the web backends")  # pragma: no cover
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "research-analyst", "advisory", tools=_WEB, supervisor=chief)
        await _agent(admin_session, org, "principal-engineer", "operator", tools=["run_claude_code"], supervisor=chief)

        warnings = await capability_warnings(
            admin_session, org.id, chief, "Audit https://example.org", settings=settings
        )

        assert warnings
        assert not any("run_claude_code" in w for w in warnings)

    async def test_a_chain_that_cannot_act_is_called_out(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "research-analyst", "advisory", supervisor=chief)

        warnings = await capability_warnings(admin_session, org.id, chief, "File the quarterly return")

        assert any("nothing in this chain can act" in w for w in warnings)

    async def test_an_order_that_only_wants_an_opinion_is_left_alone(self, admin_session: AsyncSession) -> None:
        # "Tell me what you think" is finished by reading, so a chain of advisers is
        # the right chain for it. Warning here is the noise that gets warnings ignored.
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "research-analyst", "advisory", tools=_WEB, supervisor=chief)

        warnings = await capability_warnings(
            admin_session, org.id, chief, "Check out SEO on redarchlabs.com and tell me what you think"
        )

        assert warnings == []

    async def test_a_coordinator_with_nobody_under_it_is_always_called_out(self, admin_session: AsyncSession) -> None:
        # No heuristic needed: it may not act, and it has nobody to delegate to.
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")

        warnings = await capability_warnings(admin_session, org.id, chief, "Summarise the handbook")

        assert any("no reports" in w for w in warnings)

    async def test_an_operator_in_the_chain_can_act(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "backend-engineer", "operator", tools=_ACT, supervisor=chief)

        warnings = await capability_warnings(admin_session, org.id, chief, "File the quarterly return")

        assert warnings == []


class TestItSurfacesAtDispatch:
    async def test_starting_the_order_files_the_warning(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "research-analyst", "advisory", supervisor=chief)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(
            title="SEO Optimization",
            body="Check out SEO on redarchlabs.com and tell me what you think",
            assigned_agent_id=chief.id,
        )

        await svc.set_status(wo.id, "in_progress")

        entries = await svc.list_entries(wo.id)
        assert any(e.text.startswith("⚠️ Capability gap:") for e in entries)
        alerts = (
            (await admin_session.execute(select(AgentNotification).where(AgentNotification.work_order_id == wo.id)))
            .scalars()
            .all()
        )
        assert len(alerts) == 1
        assert "may not be able to do it" in alerts[0].title

    async def test_a_chain_that_can_do_the_job_starts_quietly(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "backend-engineer", "operator", tools=_WEB + _ACT, supervisor=chief)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(
            title="SEO Optimization", body="Audit redarchlabs.com", assigned_agent_id=chief.id
        )

        await svc.set_status(wo.id, "in_progress")

        entries = await svc.list_entries(wo.id)
        assert not any(e.text.startswith("⚠️ Capability gap:") for e in entries)

    async def test_it_says_it_once_not_every_restart(self, admin_session: AsyncSession) -> None:
        org = await _org(admin_session)
        chief = await _agent(admin_session, org, "chief-of-staff", "coordinator")
        await _agent(admin_session, org, "research-analyst", "advisory", supervisor=chief)
        svc = WorkOrderService(admin_session, org.id)
        wo = await svc.create_work_order(
            title="SEO Optimization", body="Audit redarchlabs.com", assigned_agent_id=chief.id
        )
        await svc.set_status(wo.id, "in_progress")

        # A reply after the run ends dispatches again; the gap has not changed.
        await svc.reply(wo.id, "any update?")

        entries = await svc.list_entries(wo.id)
        assert len([e for e in entries if e.text.startswith("⚠️ Capability gap:")]) == 1
