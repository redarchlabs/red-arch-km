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
    ASK_HUMAN,
    CONSULT_PEER,
    DELEGATE_TASK,
    ESCALATE,
    REPLY_TO_PEER,
    REQUEST_REVIEW,
    DelegationError,
)
from api.services.agents.kind_gate import kind_gate
from api.services.agents.runtime import RunFinished, RunParked
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
    tool_call_id: str | None = None


@dataclass
class _FakeQuestion:
    """The open-question row reply_to_peer answers."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: str = "pending"


@pytest.fixture
def harness(monkeypatch):
    """Patch the DB-touching seams: agent lookup, run creation, notify, diary, and
    the question ledger that ask_human / consult_peer / reply_to_peer ride on."""

    class H:
        roster: list[FakeAgent] = []
        runs: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        answers: list[dict[str, Any]] = []
        # What the asking run's trigger looks like (drives the consult depth cap).
        run_trigger: str = "manual"
        # Whether recording an answer actually resumed the asker.
        resumed: bool = True
        pending_question: _FakeQuestion | None = None

    h = H()
    h.roster, h.runs, h.notes, h.questions, h.answers = [], [], [], [], []
    h.run_trigger, h.resumed, h.pending_question = "manual", True, None

    async def _resolve(session, org_id, ref):
        wanted = str(ref).strip().lower()
        return next((a for a in h.roster if a.name.lower() == wanted or str(a.id) == wanted), None)

    class _Runs:
        def __init__(self, *a, **kw):
            pass

        async def create_run(self, **kwargs):
            h.runs.append(kwargs)
            return type("R", (), {"id": uuid.uuid4()})()

        async def get_run(self, run_id):
            return type("R", (), {"id": run_id, "trigger": h.run_trigger})()

    class _Questions:
        def __init__(self, *a, **kw):
            pass

        async def pending_for_peer_run(self, run_id):
            return h.pending_question

    async def _create_question(session, org_id, **kwargs):
        h.questions.append(kwargs)
        return _FakeQuestion()

    async def _record_answer(session, org_id, question, **kwargs):
        h.answers.append(kwargs)
        return delegation.questions.AnswerOutcome(question=question, resumed=h.resumed)

    async def _notify(session, org_id, **kwargs):
        h.notes.append(kwargs)

    async def _get(self, agent_id):
        return next((a for a in h.roster if a.id == agent_id), None)

    monkeypatch.setattr(delegation, "resolve_agent", _resolve)
    monkeypatch.setattr(delegation, "AgentRunRepository", _Runs)
    monkeypatch.setattr(delegation, "AgentQuestionRepository", _Questions)
    monkeypatch.setattr(delegation.questions, "create_question", _create_question)
    monkeypatch.setattr(delegation.questions, "record_answer", _record_answer)
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
    """A consult *blocks*. It used to file a notification and return "sent", which
    read as success while the answer went nowhere — the agent asked a question it
    could never receive a reply to. The handler now parks the run and the peer's
    answer arrives as this call's result, so these assert the parking, the peer run
    it creates, and the routing rules that still bound who may be reached."""

    async def test_consulting_parks_the_run_and_queues_the_peer(self, harness) -> None:
        caller = FakeAgent("backend-engineer")
        peer = FakeAgent("security-analyst", kind="advisory")
        harness.roster = [caller, peer]
        ctx = FakeCtx(agent=caller, run_id=uuid.uuid4(), tool_call_id="call_1")

        with pytest.raises(RunParked) as parked:
            await CONSULT_PEER.handler(ctx, {"agent": "security-analyst", "question": "Is this token safe?"})

        assert parked.value.wait_kind == "consult"
        assert parked.value.payload["peer"] == "security-analyst"
        # The peer gets a real run — that is what makes an answer possible at all.
        assert len(harness.runs) == 1
        queued = harness.runs[0]
        assert queued["agent_id"] == peer.id
        assert queued["trigger"] == "consult"
        assert queued["status"] == "queued"
        assert "Is this token safe?" in queued["input"]["task"]
        # And the question is recorded against the exact call that blocked, which is
        # the only way the answer can be routed back into the parked turn.
        assert harness.questions[0]["tool_call_id"] == "call_1"
        assert harness.questions[0]["audience"] == "agent"

    @pytest.mark.parametrize("kind", ["coordinator", "operator"])
    async def test_refuses_a_non_advisory_target(self, harness, kind: str) -> None:
        """A consult carries no authority, so it may only reach a class that cannot
        act on it — otherwise it is work assigned off the org chart."""
        caller = FakeAgent("backend-engineer")
        peer = FakeAgent("someone", kind=kind)
        harness.roster = [caller, peer]

        out = await CONSULT_PEER.handler(
            FakeCtx(agent=caller, run_id=uuid.uuid4(), tool_call_id="c"), {"agent": "someone", "question": "?"}
        )

        assert "advisory" in out["error"]
        assert harness.runs == []

    async def test_a_rejected_consult_queues_nothing(self, harness) -> None:
        """A refused route must not leave a peer run behind to burn tokens."""
        caller = FakeAgent("backend-engineer")
        harness.roster = [caller]

        await CONSULT_PEER.handler(
            FakeCtx(agent=caller, run_id=uuid.uuid4(), tool_call_id="c"), {"agent": "ghost", "question": "?"}
        )

        assert harness.runs == [] and harness.questions == []

    async def test_an_agent_cannot_consult_itself(self, harness) -> None:
        caller = FakeAgent("security-analyst", kind="advisory")
        harness.roster = [caller]

        out = await CONSULT_PEER.handler(
            FakeCtx(agent=caller, run_id=uuid.uuid4(), tool_call_id="c"),
            {"agent": "security-analyst", "question": "?"},
        )

        assert "yourself" in out["error"]

    async def test_a_consult_may_not_itself_consult(self, harness) -> None:
        """Depth cap. Two advisors that consult each other would queue runs forever,
        and every hop is a full LLM run."""
        caller = FakeAgent("security-analyst", kind="advisory")
        peer = FakeAgent("qa-engineer", kind="advisory")
        harness.roster = [caller, peer]
        harness.run_trigger = "consult"

        out = await CONSULT_PEER.handler(
            FakeCtx(agent=caller, run_id=uuid.uuid4(), tool_call_id="c"), {"agent": "qa-engineer", "question": "?"}
        )

        assert "may not itself consult" in out["error"]
        assert harness.runs == []

    async def test_unknown_peer_is_reported(self, harness) -> None:
        caller = FakeAgent("backend-engineer")
        harness.roster = [caller]

        out = await CONSULT_PEER.handler(
            FakeCtx(agent=caller, run_id=uuid.uuid4(), tool_call_id="c"), {"agent": "ghost", "question": "?"}
        )

        assert "Unknown peer" in out["error"]

    async def test_outside_a_run_it_declines_rather_than_pretending(self, harness) -> None:
        """The interactive console executes tools with no run to suspend. Better an
        explicit refusal than a question nobody is parked on."""
        caller = FakeAgent("backend-engineer")
        peer = FakeAgent("security-analyst", kind="advisory")
        harness.roster = [caller, peer]

        out = await CONSULT_PEER.handler(FakeCtx(agent=caller), {"agent": "security-analyst", "question": "?"})

        assert "only available inside an agent run" in out["error"]
        assert harness.runs == []


class TestAskHuman:
    async def test_parks_the_run_and_notifies_a_person(self, harness) -> None:
        agent = FakeAgent("backend-engineer")
        harness.roster = [agent]
        ctx = FakeCtx(agent=agent, run_id=uuid.uuid4(), tool_call_id="call_9")

        with pytest.raises(RunParked) as parked:
            await ASK_HUMAN.handler(ctx, {"question": "Which region?", "context": "Deploying the API"})

        assert parked.value.wait_kind == "question"
        assert harness.questions[0]["audience"] == "human"
        assert harness.questions[0]["tool_call_id"] == "call_9"
        # Nobody answers what they never see.
        assert harness.notes[0]["kind"] == "question"
        assert harness.notes[0]["recipient_role"] == "org_admin"
        assert "Which region?" in harness.notes[0]["body"]

    async def test_an_empty_question_is_refused(self, harness) -> None:
        agent = FakeAgent("backend-engineer")
        harness.roster = [agent]

        out = await ASK_HUMAN.handler(FakeCtx(agent=agent, run_id=uuid.uuid4(), tool_call_id="c"), {"question": "  "})

        assert "required" in out["error"]
        assert harness.questions == [] and harness.notes == []

    async def test_outside_a_run_it_declines(self, harness) -> None:
        agent = FakeAgent("backend-engineer")
        harness.roster = [agent]

        out = await ASK_HUMAN.handler(FakeCtx(agent=agent), {"question": "Which region?"})

        assert "only available inside an agent run" in out["error"]
        assert harness.notes == []


class TestReplyToPeer:
    async def test_answers_the_consult_and_ends_the_run(self, harness) -> None:
        advisor = FakeAgent("security-analyst", kind="advisory")
        harness.roster = [advisor]
        harness.pending_question = _FakeQuestion()

        with pytest.raises(RunFinished) as finished:
            await REPLY_TO_PEER.handler(
                FakeCtx(agent=advisor, run_id=uuid.uuid4()), {"answer": "Rotate it; the scope is too broad."}
            )

        assert finished.value.status == "done"
        assert finished.value.payload["output"]["answer"] == "Rotate it; the scope is too broad."
        assert harness.answers[0]["answer"] == "Rotate it; the scope is too broad."
        assert harness.answers[0]["by_agent_id"] == advisor.id

    async def test_refuses_when_nobody_is_waiting(self, harness) -> None:
        """Offered to every agent, so it must say plainly when it does not apply
        rather than ending a run that had real work left."""
        advisor = FakeAgent("security-analyst", kind="advisory")
        harness.roster = [advisor]
        harness.pending_question = None

        out = await REPLY_TO_PEER.handler(FakeCtx(agent=advisor, run_id=uuid.uuid4()), {"answer": "Sure."})

        assert "No one is waiting on you" in out["error"]

    async def test_reports_an_asker_that_gave_up_instead_of_completing(self, harness) -> None:
        """If the asking run ended while this one was thinking, the answer is
        recorded but delivered to nobody — the advisor must not be told it landed."""
        advisor = FakeAgent("security-analyst", kind="advisory")
        harness.roster = [advisor]
        harness.pending_question = _FakeQuestion()
        harness.resumed = False

        out = await REPLY_TO_PEER.handler(FakeCtx(agent=advisor, run_id=uuid.uuid4()), {"answer": "Rotate it."})

        assert out["answered"] is False
        assert "no longer waiting" in out["reason"]


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

    def test_every_kind_may_answer_a_consult_and_ask_a_person(self) -> None:
        """An advisory agent that could not call reply_to_peer would be consulted
        and have no way to answer, and any kind can hit something only a person
        knows — so neither tool may be gated to a subset of the roster."""
        for kind in ("coordinator", "advisory", "operator"):
            assert kind_gate(kind, REPLY_TO_PEER) is None
            assert kind_gate(kind, ASK_HUMAN) is None

    def test_operator_may_use_the_whole_protocol(self) -> None:
        for spec in (DELEGATE_TASK, CONSULT_PEER, REPLY_TO_PEER, REQUEST_REVIEW, ESCALATE, ASK_HUMAN):
            assert kind_gate("operator", spec) is None

    def test_categories_match_the_governance_they_claim(self) -> None:
        assert DELEGATE_TASK.category == Category.DELEGATE
        for spec in (CONSULT_PEER, REPLY_TO_PEER, REQUEST_REVIEW, ESCALATE, ASK_HUMAN):
            assert spec.category == Category.ESCALATE

    def test_asking_a_question_is_never_itself_gated_on_approval(self) -> None:
        """Under high_touch autonomy the runtime forces every side-effecting tool to
        ASK. If asking were side-effecting, an agent would need a human's approval
        to ask that human a question — a deadlock dressed as governance."""
        assert ASK_HUMAN.side_effecting is False
        assert CONSULT_PEER.side_effecting is False

    def test_the_protocol_is_actually_offered_to_agents(self) -> None:
        """These are wired in via loader.load_agent_tools; a roster of personas is
        inert if the coordination tools are never handed to the runtime."""
        names = {spec.name for spec in delegation.delegation_tool_specs()}
        assert names == {
            "delegate_task",
            "escalate",
            "consult_peer",
            "reply_to_peer",
            "request_review",
            "ask_human",
        }
