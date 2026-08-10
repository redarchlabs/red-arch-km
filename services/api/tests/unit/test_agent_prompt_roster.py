"""The system prompt names who an agent can hand work to.

Nothing used to. The roster existed only inside the error you get back for naming a
colleague that is not yours — which an agent has to guess a name to see. Caught live
on the SEO work order: a chief-of-staff was told to "route the crawl through the
engineering chain", worked out on its own that it wanted the technical-project-manager,
could name nobody to send it to, and escalated to a human instead — twice — then marked
every remaining step blocked. Its own direct report `program-manager` owned that branch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from api.services.agents.prompts import ROSTER_CAP, build_system_prompt

pytestmark = pytest.mark.unit


@dataclass
class FakeAgent:
    name: str
    kind: str = "operator"
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    display_name: str | None = None
    persona: str | None = None


class TestTheRosterIsNamed:
    def test_direct_reports_are_listed_with_what_they_are(self) -> None:
        chief = FakeAgent("chief-of-staff", kind="coordinator")
        reports = [
            FakeAgent("program-manager", kind="coordinator"),
            FakeAgent("research-analyst", kind="advisory"),
        ]

        prompt = build_system_prompt(chief, reports=reports)

        assert "program-manager" in prompt
        assert "research-analyst" in prompt
        # The kind is the routing fact: a coordinator can pass work on, an advisory
        # agent is a leaf. Bare names would send the crawl back to the researcher.
        assert "coordinator" in prompt
        assert "cannot act and cannot delegate onward" in prompt

    def test_it_says_a_skill_further_down_is_still_reachable(self) -> None:
        """The load-bearing sentence. delegate_task is direct-reports-only, so an
        agent that reads the list literally concludes two levels down is unreachable
        and escalates instead of handing it to the branch that owns it."""
        chief = FakeAgent("chief-of-staff", kind="coordinator")

        prompt = build_system_prompt(chief, reports=[FakeAgent("program-manager", kind="coordinator")])

        assert "delegate to the coordinator whose branch owns it" in prompt

    def test_consultable_advisors_are_named_separately(self) -> None:
        chief = FakeAgent("chief-of-staff", kind="coordinator")

        prompt = build_system_prompt(
            chief,
            reports=[FakeAgent("program-manager", kind="coordinator")],
            advisors=[FakeAgent("seo-specialist", kind="advisory")],
        )

        assert "consult_peer anywhere in the org: seo-specialist" in prompt

    def test_an_agent_with_nobody_under_it_gets_no_roster_section(self) -> None:
        # Silence beats an empty heading: a "your reports:" block with nothing in it
        # reads as a loading failure rather than as a leaf position on the chart.
        solo = FakeAgent("research-analyst", kind="advisory")

        prompt = build_system_prompt(solo)

        assert "DIRECT REPORTS" not in prompt

    def test_a_large_roster_is_truncated_rather_than_flooding_the_window(self) -> None:
        chief = FakeAgent("chief-of-staff", kind="coordinator")
        many = [FakeAgent(f"agent-{i:02d}") for i in range(ROSTER_CAP + 5)]

        prompt = build_system_prompt(chief, reports=many)

        assert "and 5 more reports" in prompt
        assert f"agent-{ROSTER_CAP:02d}" not in prompt

    def test_the_roster_does_not_displace_the_work_order_instructions(self) -> None:
        chief = FakeAgent("chief-of-staff", kind="coordinator")

        prompt = build_system_prompt(
            chief,
            work_order_title="SEO Optimization",
            reports=[FakeAgent("program-manager", kind="coordinator")],
        )

        assert "SEO Optimization" in prompt
        assert "program-manager" in prompt
        assert "set_work_order_tasks" in prompt
