"""Integration tests for token-engine retry (BPMN error-handling phase).

A retryable task failure parks the token as ``waiting/retry`` with a backoff and
is reactivated by the timer sweep; on exhaustion the run fails and is dead-
lettered. Retry is opt-in via ``node.data.retry`` — a task without a policy keeps
the legacy fail-fast behaviour. Uses ``send_webhook`` with an empty allowlist as a
deterministic, network-free failure (rejected at the SSRF guard).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from api.models.org import Org
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowTokenRepository,
    WorkflowVersionRepository,
)
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.engine import TokenEngine
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _seed(admin_session: AsyncSession, definition: dict):
    await set_tenant(admin_session, None)
    org = Org(name=f"WFR-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()

    await set_tenant(admin_session, str(org.id))
    entity = await EntityService(admin_session, org.id).create_definition(
        EntityDefinitionCreate(
            name="Thing", slug="thing",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    wf_repo = WorkflowRepository(admin_session, org.id)
    ver_repo = WorkflowVersionRepository(admin_session, org.id)
    workflow = await wf_repo.create(name="WF", entity_definition_id=entity.id, description=None)
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
        input_snapshot={"before": None, "after": {}},
        depth=0,
    )
    await admin_session.commit()
    return org, workflow, run


def _webhook_task(node_id: str, *, retry: dict | None = None, continue_on_error: bool = False) -> dict:
    """A task that always fails without network I/O (empty allowlist ⇒ SSRF guard
    rejects the host before any request)."""
    data: dict = {
        "task_type": "send",
        "action_type": "send_webhook",
        "config": {"url": "https://hooks.example.com/x"},
    }
    if retry is not None:
        data["retry"] = retry
    if continue_on_error:
        data["continue_on_error"] = True
    return {"id": node_id, "type": "task", "data": data}


def _graph(task_node: dict) -> dict:
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {}},
            task_node,
            {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"id": "e0", "source": "start", "target": task_node["id"]},
            {"id": "e1", "source": task_node["id"], "target": "end"},
        ],
    }


async def _reload_run(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).get(run.id, run.created_at)


async def _steps(admin_session: AsyncSession, run):
    return await WorkflowRunRepository(admin_session, run.org_id).steps_for_run(run.id)


async def _tokens(admin_session: AsyncSession, run):
    return await WorkflowTokenRepository(admin_session, run.org_id).list_for_run(run.id)


async def _resume_and_drive(engine: TokenEngine, admin_session: AsyncSession, run):
    """Reactivate due retry tokens and re-drive, the way the worker does.

    The worker resumes + advances on a fresh session each tick, so it never sees
    stale ORM state. This long-lived test session HAS the parked token in its
    identity map (from the prior drive + assertions), and ``resume_due_tokens``
    reactivates it with a raw UPDATE that bypasses the map — so we expunge and
    reload to read fresh state before re-driving, matching production semantics.
    """
    reactivated = (await engine.resume_due_tokens())["reactivated"]
    await admin_session.commit()
    admin_session.expunge_all()
    fresh = await _reload_run(admin_session, run)
    await engine.drive_run(fresh)
    await admin_session.commit()
    return reactivated, fresh


async def test_retry_parks_then_exhausts_and_dead_letters(admin_session: AsyncSession) -> None:
    definition = _graph(_webhook_task("w", retry={"max_attempts": 3, "base_delay_seconds": 0}))
    org, _wf, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)  # empty allowlist ⇒ webhook always fails
    await engine.start_run(run, definition)
    await admin_session.commit()

    # Attempt 1 fails → token parks waiting/retry, step is 'retrying', run waiting.
    await engine.drive_run(run)
    await admin_session.commit()
    retry_tokens = [t for t in await _tokens(admin_session, run) if t.wait_kind == "retry"]
    assert len(retry_tokens) == 1
    assert retry_tokens[0].status == "waiting"
    steps = await _steps(admin_session, run)
    assert [s.status for s in steps] == ["retrying"]
    assert steps[0].attempts == 1
    assert steps[0].max_attempts == 3
    assert steps[0].next_retry_at is not None
    assert (await _reload_run(admin_session, run)).status == "waiting"

    # Attempt 2 (resume the due token, then drive): fails again → retrying (2).
    reactivated, run = await _resume_and_drive(engine, admin_session, run)
    assert reactivated == 1
    assert [s.status for s in await _steps(admin_session, run)] == ["retrying", "retrying"]

    # Attempt 3: exhausted → terminal failure + dead-letter.
    reactivated, run = await _resume_and_drive(engine, admin_session, run)
    assert reactivated == 1
    steps = await _steps(admin_session, run)
    assert [s.status for s in steps] == ["retrying", "retrying", "failed"]
    assert steps[-1].attempts == 3
    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "failed"
    assert fresh.dead_letter is True
    # No live tokens remain.
    assert all(t.status == "dead" for t in await _tokens(admin_session, run))


async def test_no_retry_policy_is_legacy_fail_fast(admin_session: AsyncSession) -> None:
    """A failing task WITHOUT a retry policy fails on the first attempt (byte-for-
    byte the legacy walker behaviour) — one failed step, run failed, dead-lettered."""
    definition = _graph(_webhook_task("w"))  # no retry
    org, _wf, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()
    await engine.drive_run(run)
    await admin_session.commit()

    steps = await _steps(admin_session, run)
    assert [s.status for s in steps] == ["failed"]
    assert steps[0].attempts == 1
    assert (await _reload_run(admin_session, run)).status == "failed"
    # No retry parking happened.
    assert not [t for t in await _tokens(admin_session, run) if t.wait_kind == "retry"]


async def test_continue_on_error_swallows_exhausted_failure(admin_session: AsyncSession) -> None:
    """After retries are exhausted, continue_on_error follows the normal out-edge
    (the run completes) instead of failing — and does NOT dead-letter."""
    definition = _graph(
        _webhook_task("w", retry={"max_attempts": 2, "base_delay_seconds": 0}, continue_on_error=True)
    )
    org, _wf, run = await _seed(admin_session, definition)
    engine = TokenEngine(admin_session)
    await engine.start_run(run, definition)
    await admin_session.commit()

    # Attempt 1 → retrying.
    await engine.drive_run(run)
    await admin_session.commit()
    assert [s.status for s in await _steps(admin_session, run)] == ["retrying"]

    # Attempt 2 exhausts the budget; continue_on_error advances to the end event.
    _reactivated, run = await _resume_and_drive(engine, admin_session, run)

    steps = await _steps(admin_session, run)
    assert [s.status for s in steps] == ["retrying", "failed"]  # 2nd attempt failed but was swallowed
    fresh = await _reload_run(admin_session, run)
    assert fresh.status == "succeeded"
    assert fresh.dead_letter is False
