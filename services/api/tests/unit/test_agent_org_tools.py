"""Unit tests for the agent↔agent coordination protocol (services/agents/delegation.py).

The org chart is enforced in two independent places, and a change to either one
silently widens what an agent may do:

* the **kind-gate**, which decides whether a governance class may originate a
  category of message at all (a coordinator delegates; an advisory agent may only
  advise), and
* the **routing rules** inside each handler, which decide *who* a specific message
  may reach (a direct report, an advisory peer, your own supervisor).

The integration suite covers delegation against a real database; these pin the
policy itself with fakes, so a rule change fails here loudly and immediately
rather than only in a DB-backed test that happens to exercise it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from api.services.agents import delegation
from api.services.agents.delegation import (
    CONSULT_PEER,
    DELEGATE_TASK,
    ESCALATE,
    REQUEST_REVIEW,
    DelegationError,
)
from api.services.agents.kind_gate import kind_gate
from api.services.agents.tools.spec import Category

pytestmark = pytest.mark.unit


@dataclass
class FakeAgent:
    name: str
    kind: str = "operator"
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    supervisor_id: uuid.UUID | None = None
    enabled: bool = True
    provider: str = "openai"
    model: str = "gpt-4.1-mini"


@dataclass
class FakeCtx:
    """Stands in for ToolContext — only the fields these handlers read."""

    agent: FakeAgent
    session: Any = None
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    settings: Any = None
    run_id: uuid.UUID | None = None
    work_order_id: uuid.UUID | None = None


@pytest.fixture
def harness(monkeypatch):
    """Patch the DB-touching seams: agent lookup, run creation, notify, diary."""

    class H:
        roster: list[FakeAgent] = []
        runs: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = []

    h = H()
    h.roster, h.runs, h.notes = [], [], []

    async def _resolve(session, org_id, ref):
        wanted = str(ref).strip().lower()
        return next((a for a in h.roster if a.name.lower() == wanted or str(a.id) == wanted), None)

    class _Runs:
        def __init__(self, *a, **kw):
            pass

        async def create_run(self, **kwargs):
            h.runs.append(kwargs)
            return type("R", (), {"id": uuid.uuid4()})()

    async def _notify(session, org_id, **kwargs):
        h.notes.append(kwargs)

    async def _get(self, agent_id):
        return next((a for a in h.roster if a.id == agent_id), None)

    monkeypatch.setattr(delegation, "resolve_agent", _resolve)
    monkeypatch.setattr(delegation, "AgentRunRepository", _Runs)
    monkeypatch.setattr(delegation, "create_notification", _notify)
    monkeypatch.setattr(delegation.AgentRepository, "get", _get, raising=False)
    # The work-order diary is exercised by the integration suite; keep these pure.
    monkeypatch.setattr(delegation, "_diary", lambda ctx, text: _noop())
    return h


async def _noop() -> None:
    return None


class TestDelegateTask:
    async def test_delegates_to_a_direct_report(self, harness) -> None:
        boss = FakeAgent("tpm", kind="coordinator")
        report = FakeAgent("backend-engineer", supervisor_id=boss.id)
        harness.roster = [boss, report]

        out = await DELEGATE_TASK.handler(
            FakeCtx(agent=boss), {"agent": "backend-engineer", "task": "Add the endpoint"}
        )

        assert out["delegated_to"] == "backend-engineer"
        assert out["status"] == "queued"
        # Queued, not running: the existing advance-runs sweep drives it, so the
        # protocol adds no second execution path.
        assert harness.runs[0]["status"] == "queued"
        assert harness.runs[0]["trigger"] == "delegation"
        assert harness.runs[0]["agent_id"] == report.id
        assert harness.runs[0]["input"]["task"] == "Add the endpoint"

    async def test_child_run_is_linked_to_the_delegating_run(self, harness) -> None:
        """parent_run_id is what makes a chain of delegated work reconstructable."""
        boss = FakeAgent("tpm", kind="coordinator")
        report = FakeAgent("engineer", supervisor_id=boss.id)
        harness.roster = [boss, report]
        parent = uuid.uuid4()

        await DELEGATE_TASK.handler(FakeCtx(agent=boss, run_id=parent), {"agent": "engineer", "task": "x"})

        assert harness.runs[0]["parent_run_id"] == parent

    async def test_refuses_a_grandchild(self, harness) -> None:
        """Skipping a level bypasses the supervisor accountable for the work."""
        boss = FakeAgent("pm", kind="coordinator")
        middle = FakeAgent("tpm", kind="coordinator", supervisor_id=boss.id)
        grandchild = FakeAgent("engineer", supervisor_id=middle.id)
        harness.roster = [boss, middle, grandchild]

        out = await DELEGATE_TASK.handler(FakeCtx(agent=boss), {"agent": "engineer", "task": "Do it"})

        assert "direct reports" in out["error"]
        assert harness.runs == []

    async def test_refuses_to_delegate_upward(self, harness) -> None:
        boss = FakeAgent("pm", kind="coordinator")
        report = FakeAgent("tpm", kind="coordinator", supervisor_id=boss.id)
        harness.roster = [boss, report]

        out = await DELEGATE_TASK.handler(FakeCtx(agent=report), {"agent": "pm", "task": "You do it"})

        assert "direct reports" in out["error"]
        assert harness.runs == []

    async def test_refuses_a_sibling(self, harness) -> None:
        boss = FakeAgent("principal-engineer", kind="coordinator")
        a = FakeAgent("frontend-engineer", supervisor_id=boss.id)
        b = FakeAgent("backend-engineer", supervisor_id=boss.id)
        harness.roster = [boss, a, b]

        out = await DELEGATE_TASK.handler(FakeCtx(agent=a), {"agent": "backend-engineer", "task": "x"})

        assert "direct reports" in out["error"]
        assert harness.runs == []

    async def test_unknown_agent_is_reported(self, harness) -> None:
        boss = FakeAgent("tpm", kind="coordinator")
        harness.roster = [boss]

        out = await DELEGATE_TASK.handler(FakeCtx(agent=boss), {"agent": "ghost", "task": "x"})

        assert "Unknown target agent" in out["error"]

    async def test_requires_both_arguments(self, harness) -> None:
        boss = FakeAgent("tpm", kind="coordinator")
        harness.roster = [boss]

        assert "required" in (await DELEGATE_TASK.handler(FakeCtx(agent=boss), {"task": "x"}))["error"]
        assert "required" in (await DELEGATE_TASK.handler(FakeCtx(agent=boss), {"agent": "x"}))["error"]
        assert harness.runs == []

    async def test_delegate_raises_rather_than_queueing_for_a_non_report(self, harness) -> None:
        """The service-level guard, independent of the tool wrapper."""
        boss = FakeAgent("pm", kind="coordinator")
        stranger = FakeAgent("unrelated")
        harness.roster = [boss, stranger]

        with pytest.raises(DelegationError):
            await delegation.delegate(None, uuid.uuid4(), boss, "unrelated", "task", run_id=None, work_order_id=None)
        assert harness.runs == []


class TestConsultPeer:
    async def test_consults_an_advisory_agent(self, harness) -> None:
        caller = FakeAgent("backend-engineer")
        peer = FakeAgent("security-analyst", kind="advisory")
        harness.roster = [caller, peer]

        out = await CONSULT_PEER.handler(
            FakeCtx(agent=caller), {"agent": "security-analyst", "question": "Is this token safe?"}
        )

        assert out["consulted"] == "security-analyst"
        assert harness.notes[0]["body"] == "Is this token safe?"

    @pytest.mark.parametrize("kind", ["coordinator", "operator"])
    async def test_refuses_a_non_advisory_target(self, harness, kind: str) -> None:
        """A consult carries no authority, so it may only reach a class that cannot
        act on it — otherwise it is work assigned off the org chart."""
        caller = FakeAgent("backend-engineer")
        peer = FakeAgent("someone", kind=kind)
        harness.roster = [caller, peer]

        out = await CONSULT_PEER.handler(FakeCtx(agent=caller), {"agent": "someone", "question": "?"})

        assert "advisory" in out["error"]
        assert harness.notes == []

    async def test_consulting_never_queues_a_run(self, harness) -> None:
        """A consult is non-binding: it must not create work for the peer."""
        caller = FakeAgent("backend-engineer")
        peer = FakeAgent("qa-engineer", kind="advisory")
        harness.roster = [caller, peer]

        await CONSULT_PEER.handler(FakeCtx(agent=caller), {"agent": "qa-engineer", "question": "?"})

        assert harness.runs == []

    async def test_unknown_peer_is_reported(self, harness) -> None:
        caller = FakeAgent("backend-engineer")
        harness.roster = [caller]

        out = await CONSULT_PEER.handler(FakeCtx(agent=caller), {"agent": "ghost", "question": "?"})

        assert "Unknown peer" in out["error"]


class TestRequestReviewAndEscalate:
    async def test_review_goes_to_the_supervisor(self, harness) -> None:
        boss = FakeAgent("principal-engineer", kind="coordinator")
        worker = FakeAgent("frontend-engineer", supervisor_id=boss.id)
        harness.roster = [boss, worker]

        out = await REQUEST_REVIEW.handler(FakeCtx(agent=worker), {"summary": "Shipped the form"})

        assert out["review_requested_from"] == "principal-engineer"
        # Addressed to the supervisor, so it must not also page every org admin.
        assert harness.notes[0]["recipient_role"] is None

    async def test_review_from_the_apex_reaches_a_human(self, harness) -> None:
        """With nobody above them, the top of the chart escalates out to a person."""
        apex = FakeAgent("program-manager", kind="coordinator", supervisor_id=None)
        harness.roster = [apex]

        out = await REQUEST_REVIEW.handler(FakeCtx(agent=apex), {"summary": "portfolio ready"})

        assert out["review_requested_from"] == "human reviewer"
        assert harness.notes[0]["recipient_role"] == "org_admin"

    async def test_review_requires_a_summary(self, harness) -> None:
        worker = FakeAgent("engineer", supervisor_id=uuid.uuid4())
        harness.roster = [worker]

        assert "required" in (await REQUEST_REVIEW.handler(FakeCtx(agent=worker), {}))["error"]
        assert harness.notes == []

    async def test_escalation_routes_the_same_way(self, harness) -> None:
        boss = FakeAgent("tpm", kind="coordinator")
        worker = FakeAgent("engineer", supervisor_id=boss.id)
        harness.roster = [boss, worker]

        out = await ESCALATE.handler(FakeCtx(agent=worker), {"reason": "Needs a product call"})

        assert out["escalated_to"] == "tpm"
        assert harness.notes[0]["kind"] == "escalation"

    async def test_escalation_requires_a_reason(self, harness) -> None:
        worker = FakeAgent("engineer", supervisor_id=uuid.uuid4())
        harness.roster = [worker]

        assert "required" in (await ESCALATE.handler(FakeCtx(agent=worker), {}))["error"]


class TestKindGateCoversTheProtocol:
    """The other half of the policy: which classes may originate each message."""

    def test_coordinator_may_delegate_and_escalate(self) -> None:
        assert kind_gate("coordinator", DELEGATE_TASK) is None
        assert kind_gate("coordinator", REQUEST_REVIEW) is None

    def test_advisory_may_advise_but_never_delegate(self) -> None:
        assert kind_gate("advisory", CONSULT_PEER) is None
        assert kind_gate("advisory", REQUEST_REVIEW) is None
        assert kind_gate("advisory", ESCALATE) is None
        # Handing out work is not advice.
        assert kind_gate("advisory", DELEGATE_TASK) is not None

    def test_operator_may_use_the_whole_protocol(self) -> None:
        for spec in (DELEGATE_TASK, CONSULT_PEER, REQUEST_REVIEW, ESCALATE):
            assert kind_gate("operator", spec) is None

    def test_categories_match_the_governance_they_claim(self) -> None:
        assert DELEGATE_TASK.category == Category.DELEGATE
        for spec in (CONSULT_PEER, REQUEST_REVIEW, ESCALATE):
            assert spec.category == Category.ESCALATE

    def test_the_protocol_is_actually_offered_to_agents(self) -> None:
        """These are wired in via loader.load_agent_tools; a roster of personas is
        inert if the coordination tools are never handed to the runtime."""
        names = {spec.name for spec in delegation.delegation_tool_specs()}
        assert names == {"delegate_task", "escalate", "consult_peer", "request_review"}
