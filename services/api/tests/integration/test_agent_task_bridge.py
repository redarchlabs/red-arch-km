"""End-to-end integration tests for the agent_task bridge: dispatch enqueues and
parks, wire-back resumes (done/escalated), timer boundary cancels, reconciliation
heals a lost signal, and the anti-spoofing / consent / publish gates hold."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from api.models.agent import Agent
from api.models.org import Org
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowTokenRepository,
    WorkflowVersionRepository,
)
from api.services.agents import lifecycle
from api.services.workflow import agent_bridge
from api.services.workflow.engine import TokenEngine
from api.services.workflow.service import WorkflowPublishError, WorkflowService
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


def _agent_graph(agent_id: uuid.UUID | str, *, timer_seconds: int | None = None, consent: bool = True) -> dict:
    nodes = [
        {"id": "start", "type": "trigger", "data": {}},
        {
            "id": "a1",
            "type": "task",
            "data": {
                "task_type": "agent",
                "agent_id": str(agent_id),
                "task": "Categorize this: {{ after.title }}",
                "output_schema": {"category": {"type": "string", "enum": ["billing", "tech"]}},
                "capture": "triage",
            },
        },
        {"id": "berr", "type": "event", "data": {"position": "boundary", "event_type": "error", "attached_to": "a1"}},
        {
            "id": "esc",
            "type": "task",
            "data": {"task_type": "service", "action_type": "log", "config": {"message": "escalated-path"}},
        },
        {
            "id": "ok",
            "type": "task",
            "data": {"task_type": "service", "action_type": "log", "config": {"message": "done-path"}},
        },
        {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        {"id": "end2", "type": "event", "data": {"position": "end", "event_type": "none"}},
    ]
    edges = [
        {"id": "e1", "source": "start", "target": "a1"},
        {"id": "e2", "source": "a1", "target": "ok"},
        {"id": "e3", "source": "ok", "target": "end"},
        {"id": "e4", "source": "berr", "target": "esc"},
        {"id": "e5", "source": "esc", "target": "end2"},
    ]
    if timer_seconds is not None:
        nodes.append(
            {
                "id": "btimer",
                "type": "event",
                "data": {
                    "position": "boundary",
                    "event_type": "timer",
                    "attached_to": "a1",
                    "delay_seconds": timer_seconds,
                },
            }
        )
        edges.append({"id": "e6", "source": "btimer", "target": "esc"})
    return {"schema_version": 2, "nodes": nodes, "edges": edges}


async def _seed(
    admin_session: AsyncSession, *, timer_seconds: int | None = None, consent: bool = True, kind: str = "operator"
):
    await set_tenant(admin_session, None)
    org = Org(name=f"AgB-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    agent = Agent(name="triage-bot", provider="openai", model="gpt-5-mini", kind=kind, org_id=org.id)
    admin_session.add(agent)
    await admin_session.flush()

    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    workflow = await wf_repo.create(name="Triage", entity_definition_id=None, description=None)
    agent.workflow_invocable = [str(workflow.id)] if consent else []
    definition = _agent_graph(agent.id, timer_seconds=timer_seconds)
    version = await ver_repo.create(workflow_id=workflow.id, version_number=1, definition=definition)
    version.status = "published"
    version.published_at = func.now()
    await wf_repo.update(workflow, enabled=True, active_version_id=version.id)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    run = await WorkflowRunRepository(admin_session, org.id).create_run_if_absent(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        outbox_id=uuid.uuid4(),
        outbox_seq=None,
        created_at=datetime.now(UTC),
        trigger_operation="update",
        record_id=None,
        input_snapshot={"before": None, "after": {"title": "printer on fire"}},
        depth=0,
    )
    await admin_session.commit()
    return org, agent, workflow, run, definition


async def _drive(admin_session: AsyncSession, run, definition) -> TokenEngine:
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()
    return engine


async def _redrive(admin_session: AsyncSession, run) -> None:
    await TokenEngine(admin_session).drive_run(run)
    await admin_session.commit()


async def _reload_run(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).get(run.id, run.created_at)


async def _agent_run(admin_session: AsyncSession, org_id, workflow_run_id):
    from api.models.agent_run import AgentRun

    return (
        (
            await admin_session.execute(
                select(AgentRun).where(AgentRun.workflow_run_id == workflow_run_id, AgentRun.org_id == org_id)
            )
        )
        .scalars()
        .one()
    )


async def _the_token(admin_session: AsyncSession, run):
    tokens = await WorkflowTokenRepository(admin_session, run.org_id).list_for_run(run.id)
    waiting = [t for t in tokens if t.status == "waiting"]
    assert len(waiting) == 1
    return waiting[0]


class TestDispatchEnqueuesAndParks:
    async def test_first_arrival_enqueues_once_and_parks(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        await _drive(admin_session, run, definition)

        fresh = await _reload_run(admin_session, run)
        assert fresh.status == "waiting"

        agent_run = await _agent_run(admin_session, org.id, run.id)
        assert agent_run.status == "queued"
        assert agent_run.trigger == "workflow"
        assert agent_run.actor_user_id is None  # service identity, never the trigger actor
        assert agent_run.workflow_node_id == "a1"
        # Template rendered with record data; contract snapshotted.
        assert "printer on fire" in agent_run.input["task"]
        assert agent_run.input["workflow"]["output_schema"]["category"]["enum"] == ["billing", "tech"]

        token = await _the_token(admin_session, run)
        assert token.wait_kind == "agent"
        assert token.correlation_key == str(agent_run.id)
        assert token.data["_agent_run_id"] == str(agent_run.id)

        # The in-flight step is visible with the transcript link.
        steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
        agent_steps = [s for s in steps if s.node_id == "a1"]
        assert agent_steps[0].status == "running"
        assert agent_steps[0].output["agent_run_id"] == str(agent_run.id)

    async def test_agent_park_is_not_human_signalable(self, admin_session: AsyncSession) -> None:
        """Any-org-member complete-task must NOT be able to forge the agent's step."""
        org, agent, workflow, run, definition = await _seed(admin_session)
        engine = await _drive(admin_session, run, definition)
        fresh = await _reload_run(admin_session, run)
        signaled = await engine.signal_token(fresh, node_id="a1", output={"forged": True})
        assert signaled is False

    async def test_without_consent_the_step_escalates(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session, consent=False)
        await _drive(admin_session, run, definition)
        fresh = await _reload_run(admin_session, run)
        # Setup failure routed through the error boundary to the escalation path.
        assert fresh.status == "succeeded"
        steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
        assert any(s.node_id == "a1" and s.status == "failed" for s in steps)
        assert any(s.output == {"logged": "escalated-path"} for s in steps if s.output)

    async def test_non_operator_agent_is_rejected(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session, kind="advisory")
        await _drive(admin_session, run, definition)
        steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
        failed = [s for s in steps if s.node_id == "a1" and s.status == "failed"]
        assert failed and "operator" in failed[0].output["error"]


class TestWireBack:
    async def test_done_resumes_completed_path_and_captures_vars(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        await _drive(admin_session, run, definition)
        agent_run = await _agent_run(admin_session, org.id, run.id)

        # Simulate the executor finishing: output + finalize through the choke point.
        agent_run.output = {"category": "tech"}
        won = await lifecycle.finalize_run(admin_session, org.id, agent_run, status="done", total_tokens=42)
        await admin_session.commit()
        assert won is True

        await set_tenant(admin_session, str(org.id))
        fresh = await _reload_run(admin_session, run)
        await _redrive(admin_session, fresh)

        fresh = await _reload_run(admin_session, run)
        assert fresh.status == "succeeded"
        assert fresh.variables["triage"] == {"category": "tech"}  # validated object, not the audit envelope
        steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
        agent_step = next(s for s in steps if s.node_id == "a1")
        assert agent_step.status == "succeeded"
        assert agent_step.output["agent_run_id"] == str(agent_run.id)  # audit snapshot survives
        assert any(s.output == {"logged": "done-path"} for s in steps if s.output)

    async def test_escalated_routes_error_boundary(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        await _drive(admin_session, run, definition)
        agent_run = await _agent_run(admin_session, org.id, run.id)

        won = await lifecycle.finalize_run(
            admin_session, org.id, agent_run, status="escalated", error="ambiguous ticket"
        )
        await admin_session.commit()
        assert won is True

        await set_tenant(admin_session, str(org.id))
        fresh = await _reload_run(admin_session, run)
        await _redrive(admin_session, fresh)

        fresh = await _reload_run(admin_session, run)
        assert fresh.status == "succeeded"  # escalation path ran to its end event
        steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
        agent_step = next(s for s in steps if s.node_id == "a1")
        assert agent_step.status == "failed"
        assert "ambiguous ticket" in agent_step.error
        assert any(s.output == {"logged": "escalated-path"} for s in steps if s.output)

    async def test_error_run_routes_boundary_too(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        await _drive(admin_session, run, definition)
        agent_run = await _agent_run(admin_session, org.id, run.id)
        await lifecycle.finalize_run(admin_session, org.id, agent_run, status="error", error="no key")
        await admin_session.commit()

        await set_tenant(admin_session, str(org.id))
        fresh = await _reload_run(admin_session, run)
        await _redrive(admin_session, fresh)
        steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
        assert any(s.output == {"logged": "escalated-path"} for s in steps if s.output)


class TestTimeout:
    async def test_fired_timer_cancels_run_and_escalates(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session, timer_seconds=3600)
        await _drive(admin_session, run, definition)
        agent_run = await _agent_run(admin_session, org.id, run.id)
        token = await _the_token(admin_session, run)
        assert token.data.get("_armed") is True
        assert token.resume_at is not None
        # Primitive ids captured BEFORE expire_all: expired ORM attributes can't
        # lazy-load outside an awaited call on an async session.
        org_id, run_id, run_ca, token_id = org.id, run.id, run.created_at, token.id

        # Force-expire the SLA, then run the timer sweep + advance. (Re-scope
        # first: SET LOCAL resets on commit, and an unscoped session's UPDATE is
        # silently filtered to 0 rows by RLS.)
        await set_tenant(admin_session, str(org_id))
        result = await admin_session.execute(
            text("UPDATE workflow_run_tokens SET resume_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": token_id},
        )
        assert result.rowcount == 1
        await admin_session.commit()
        engine = TokenEngine(admin_session)
        resumed = await engine.resume_due_tokens(limit=500)  # high limit: shared DB carries strays
        await admin_session.commit()
        assert resumed["reactivated"] >= 1
        # The raw-SQL sweep updated rows the ORM identity map still caches from
        # earlier in this shared test session — expire so the drive sees reality.
        admin_session.expire_all()
        await set_tenant(admin_session, str(org_id))
        fresh = await WorkflowRunRepository(admin_session, org_id).get(run_id, run_ca)
        await _redrive(admin_session, fresh)

        # The zombie is dead and the human path ran.
        await admin_session.refresh(agent_run)
        assert agent_run.status == "cancelled"
        steps = await WorkflowRunRepository(admin_session, org_id).steps_for_run(run_id)
        assert any(s.output == {"logged": "escalated-path"} for s in steps if s.output)

        # A late finalize from a worker that was still mid-loop loses CAS and
        # does NOT wire back onto the moved-on token.
        agent_run.output = {"category": "tech"}
        won = await lifecycle.finalize_run(admin_session, org_id, agent_run, status="done")
        assert won is False


class TestReconciliation:
    async def test_lost_wire_back_is_healed_by_the_sweep(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        await _drive(admin_session, run, definition)
        agent_run = await _agent_run(admin_session, org.id, run.id)

        # Simulate a crash between finalize and signal: raw terminal write that
        # bypasses the lifecycle choke point entirely.
        await admin_session.execute(
            text("UPDATE agent_runs SET status='done', output='{\"category\": \"billing\"}'::jsonb WHERE id=:id"),
            {"id": agent_run.id},
        )
        await admin_session.commit()

        healed = await agent_bridge.reconcile_agent_tokens(admin_session, limit=50)
        await admin_session.commit()
        assert healed >= 1  # >= : the shared test DB may carry other orgs' strays

        await set_tenant(admin_session, str(org.id))
        fresh = await _reload_run(admin_session, run)
        await _redrive(admin_session, fresh)
        fresh = await _reload_run(admin_session, run)
        assert fresh.status == "succeeded"
        assert fresh.variables["triage"] == {"category": "billing"}

    async def test_sweep_ignores_live_runs(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        await _drive(admin_session, run, definition)
        healed = await agent_bridge.reconcile_agent_tokens(admin_session, limit=10)
        assert healed == 0


class TestCancellationPropagation:
    async def test_failed_workflow_run_cancels_linked_agent_runs(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        await _drive(admin_session, run, definition)
        agent_run = await _agent_run(admin_session, org.id, run.id)
        assert agent_run.status == "queued"

        cancelled = await agent_bridge.cancel_runs_for_workflow_run(
            admin_session, org.id, run.id, reason="workflow terminated"
        )
        await admin_session.commit()
        assert cancelled == 1
        await admin_session.refresh(agent_run)
        assert agent_run.status == "cancelled"


class TestPublishPreflight:
    async def test_publish_blocks_without_boundary(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        bad = {
            **definition,
            "nodes": [n for n in definition["nodes"] if n["id"] != "berr"],
            "edges": [e for e in definition["edges"] if e["id"] not in ("e4", "e5")],
        }
        svc = WorkflowService(admin_session, org.id)
        version = await svc.save_draft(workflow.id, bad)
        await admin_session.commit()
        with pytest.raises(WorkflowPublishError, match="error boundary"):
            await svc.publish(workflow.id, version.id)

    async def test_publish_blocks_without_consent(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session, consent=False)
        svc = WorkflowService(admin_session, org.id)
        version = await svc.save_draft(workflow.id, definition)
        await admin_session.commit()
        with pytest.raises(WorkflowPublishError, match="not opted in"):
            await svc.publish(workflow.id, version.id)

    async def test_publish_allows_a_clean_agent_graph(self, admin_session: AsyncSession) -> None:
        org, agent, workflow, run, definition = await _seed(admin_session)
        svc = WorkflowService(admin_session, org.id)
        version = await svc.save_draft(workflow.id, definition)
        await admin_session.commit()
        published = await svc.publish(workflow.id, version.id)
        assert published.status == "published"
