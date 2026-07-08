"""Integration test: a manual (BPMN "none" start event) workflow.

A manual workflow has no entity and is run on demand with caller-supplied input
variables; those inputs must reach the action layer and resolve in ``{{ inputs.x }}``
templates. Covers the token-engine path end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api.models.org import Org
from api.repositories.workflow import WorkflowRunRepository
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration

# Manual trigger declaring two inputs -> a send_email task that templates the
# recipient/subject from those inputs -> end. schema_version 2 => token engine.
_DEFINITION = {
    "schema_version": 2,
    "nodes": [
        {
            "id": "t",
            "type": "trigger",
            "data": {
                "source": "manual",
                "inputs": [
                    {"key": "email", "label": "Email", "type": "text", "required": True},
                    {"key": "name", "label": "Name", "type": "text", "required": False},
                ],
            },
        },
        {
            "id": "a",
            "type": "task",
            "data": {
                "task_type": "send",
                "action_type": "send_email",
                "config": {
                    "to": "{{ inputs.email }}",
                    "subject": "Hello {{ inputs.name }}",
                    "body": "body",
                },
            },
        },
        {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "a"},
        {"id": "e2", "source": "a", "target": "end"},
    ],
}


async def _publish_manual_workflow(session: AsyncSession, org_id: uuid.UUID):
    svc = WorkflowService(session, org_id)
    workflow = await svc.create_workflow(name="Manual", entity_definition_id=None, description=None)
    version = await svc.save_draft(workflow.id, _DEFINITION)
    await svc.publish(workflow.id, version.id)
    return workflow, version


async def test_manual_workflow_has_no_entity(admin_session: AsyncSession) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"WF-NONE-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))

    workflow, _ = await _publish_manual_workflow(admin_session, org.id)
    await admin_session.commit()

    assert workflow.entity_definition_id is None


async def test_manual_run_resolves_inputs_in_action(admin_session: AsyncSession) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"WF-INPUTS-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))

    workflow, version = await _publish_manual_workflow(admin_session, org.id)
    await admin_session.commit()

    dispatcher = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, executed = await dispatcher.run_version_manually(
        org.id,
        workflow,
        version,
        operation="manual",
        record_id=None,
        before=None,
        after=None,
        inputs={"email": "alice@example.com", "name": "Alice"},
    )
    await admin_session.commit()

    assert run.status == "succeeded"
    assert executed == 1
    assert run.trigger_operation == "manual"
    assert run.record_id is None
    # The caller-supplied inputs are persisted on the run snapshot...
    assert run.input_snapshot["inputs"] == {"email": "alice@example.com", "name": "Alice"}

    # ...and reached the action, resolving both {{ inputs.* }} tokens.
    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    send_step = next(s for s in steps if s.action_type == "send_email")
    assert send_step.status == "succeeded"
    assert send_step.output["to"] == "alice@example.com"
    assert send_step.output["subject"] == "Hello Alice"


# A legacy (schema_version 1) manual workflow: trigger -> delay -> send_email. The
# legacy walker parks at the delay and rebuilds context on resume, so this proves
# inputs survive a delay/resume cycle (regression guard for the resume path).
_LEGACY_DELAY_DEF = {
    "nodes": [
        {
            "id": "t",
            "type": "trigger",
            "data": {"source": "manual", "inputs": [{"key": "email", "label": "Email", "type": "text"}]},
        },
        {"id": "d", "type": "delay", "data": {"delay_seconds": 3600}},
        {
            "id": "a",
            "type": "action",
            "data": {"action_type": "send_email", "config": {"to": "{{ inputs.email }}", "subject": "s", "body": "b"}},
        },
    ],
    "edges": [
        {"id": "e1", "source": "t", "target": "d"},
        {"id": "e2", "source": "d", "target": "a"},
    ],
}


async def test_manual_inputs_survive_delay_resume(admin_session: AsyncSession) -> None:
    await set_tenant(admin_session, None)
    org = Org(name=f"WF-DELAY-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))

    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="ManualDelay", entity_definition_id=None, description=None)
    version = await svc.save_draft(wf.id, _LEGACY_DELAY_DEF)
    await svc.publish(wf.id, version.id)
    await admin_session.commit()

    dispatcher = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, _ = await dispatcher.run_version_manually(
        org.id,
        wf,
        version,
        operation="manual",
        record_id=None,
        before=None,
        after=None,
        inputs={"email": "delayed@example.com"},
    )
    await admin_session.commit()

    # Parked at the delay before the send_email action runs.
    assert run.status == "waiting"
    assert run.resume_node_id == "a"

    # Make it due and sweep — the post-delay action must still see the inputs.
    run.resume_at = datetime.now(UTC) - timedelta(minutes=1)  # type: ignore[assignment]
    await admin_session.flush()
    await dispatcher.resume_waiting_runs()
    await admin_session.commit()

    assert run.status == "succeeded"
    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    send_step = next(s for s in steps if s.action_type == "send_email")
    assert send_step.status == "succeeded"
    assert send_step.output["to"] == "delayed@example.com"
