"""Integration tests for dispatch hardening:

* per-event RLS downgrade is a real backstop (a scoped unit cannot touch another
  org's dynamic table even with a wrong app-level org_id),
* continue_on_error semantics,
* poison-event isolation (one event failing unexpectedly doesn't sink the batch).
"""

from __future__ import annotations

import uuid

import pytest
from api.models.org import Org
from api.repositories.custom_entity import EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.schemas.custom_entity import EntityDefinitionCreate, EntityFieldCreate
from api.services.entity_service import EntityService
from api.services.workflow.dispatcher import WorkflowDispatchService
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession

from .helpers import set_tenant

pytestmark = pytest.mark.integration


async def _new_org(session: AsyncSession, prefix: str) -> Org:
    await set_tenant(session, None)
    org = Org(name=f"{prefix}-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.commit()
    await set_tenant(session, str(org.id))
    return org


async def _ticket_entity(session: AsyncSession, org_id: uuid.UUID):
    definition = await EntityService(session, org_id).create_definition(
        EntityDefinitionCreate(
            name="Ticket",
            slug=f"ticket_{uuid.uuid4().hex[:6]}",
            fields=[EntityFieldCreate(name="Title", slug="title", field_type="text")],
        )
    )
    await session.commit()
    return definition


async def test_tenant_downgrade_blocks_cross_org_write(admin_session: AsyncSession) -> None:
    """With the session downgraded to org A, an attempt to write org B's record
    — even via a repo mis-scoped to org B — is blocked by RLS (0 rows), proving
    the per-unit downgrade is a genuine DB backstop, not just app-level scoping."""
    org_a = await _new_org(admin_session, "WF-ISO-A")
    await _ticket_entity(admin_session, org_a.id)

    org_b = await _new_org(admin_session, "WF-ISO-B")
    def_b = await _ticket_entity(admin_session, org_b.id)
    fields_b = await EntityFieldRepository(admin_session, org_b.id).list_for_definition(def_b.id)
    repo_b = DynamicEntityRepository(admin_session, org_b.id, def_b, fields_b)
    rec_b = await repo_b.create({"title": "B-original"})
    rec_b_id = uuid.UUID(str(rec_b["id"]))
    await admin_session.commit()

    disp = WorkflowDispatchService(admin_session, public_base_url="http://x")
    # Downgrade to org A (as the per-event path does), then try to mutate B.
    await disp._enter_tenant(org_a.id)
    repo_b_scoped_a = DynamicEntityRepository(admin_session, org_b.id, def_b, fields_b)
    updated = await repo_b_scoped_a.update(rec_b_id, {"title": "HACKED"})
    await disp._exit_tenant()

    assert updated is None  # RLS filtered the row out — write blocked

    # B's record is untouched.
    await set_tenant(admin_session, str(org_b.id))
    assert (await repo_b.get(rec_b_id))["title"] == "B-original"


def _two_action_def(target_missing_slug: str, *, continue_on_error: bool) -> dict:
    # First action fails (create_record into a non-existent entity); second logs.
    return {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
            {"id": "a1", "type": "action", "data": {
                "action_type": "create_record",
                "config": {"target_slug": target_missing_slug, "values": {}},
                "continue_on_error": continue_on_error,
            }},
            {"id": "a2", "type": "action", "data": {
                "action_type": "log", "config": {"message": "reached"},
            }},
        ],
        "edges": [
            {"id": "e0", "source": "t", "target": "a1"},
            {"id": "e1", "source": "a1", "target": "a2"},
        ],
    }


async def test_continue_on_error_true_continues(admin_session: AsyncSession) -> None:
    org = await _new_org(admin_session, "WF-COE-T")
    definition = await _ticket_entity(admin_session, org.id)
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    rec = await repo.create({"title": "x"})
    rec_id = uuid.UUID(str(rec["id"]))

    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="COE-T", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, _two_action_def("does_not_exist", continue_on_error=True))
    await svc.publish(wf.id, version.id)
    await admin_session.commit()

    disp = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, executed = await disp.run_version_manually(
        org.id, wf, version, operation="update",
        record_id=rec_id, before={"title": "x"}, after={"title": "x"},
    )
    await admin_session.commit()

    from api.repositories.workflow import WorkflowRunRepository

    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    statuses = [s.status for s in steps]
    assert statuses == ["failed", "succeeded"]  # continued past the failure
    assert executed == 1  # only the log succeeded


async def test_continue_on_error_false_stops(admin_session: AsyncSession) -> None:
    org = await _new_org(admin_session, "WF-COE-F")
    definition = await _ticket_entity(admin_session, org.id)
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)
    repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    rec = await repo.create({"title": "x"})
    rec_id = uuid.UUID(str(rec["id"]))

    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="COE-F", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, _two_action_def("does_not_exist", continue_on_error=False))
    await svc.publish(wf.id, version.id)
    await admin_session.commit()

    disp = WorkflowDispatchService(admin_session, public_base_url="http://x")
    run, executed = await disp.run_version_manually(
        org.id, wf, version, operation="update",
        record_id=rec_id, before={"title": "x"}, after={"title": "x"},
    )
    await admin_session.commit()

    from api.repositories.workflow import WorkflowRunRepository

    steps = await WorkflowRunRepository(admin_session, org.id).steps_for_run(run.id)
    assert [s.status for s in steps] == ["failed"]  # stopped; second step never created
    assert executed == 0
    assert run.status == "failed"


async def test_poison_event_is_isolated(
    admin_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If processing one event raises unexpectedly, that event is marked skipped
    and the sibling event in the same batch still succeeds."""
    org = await _new_org(admin_session, "WF-POISON")
    definition = await _ticket_entity(admin_session, org.id)
    fields = await EntityFieldRepository(admin_session, org.id).list_for_definition(definition.id)

    from api.repositories.workflow import OutboxWriter

    repo = DynamicEntityRepository(
        admin_session, org.id, definition, fields, outbox=OutboxWriter(admin_session)
    )
    good_def = {
        "nodes": [
            {"id": "t", "type": "trigger", "data": {"operations": ["update"]}},
            {"id": "a", "type": "action",
             "data": {"action_type": "log", "config": {"message": "ok"}}},
        ],
        "edges": [{"id": "e0", "source": "t", "target": "a"}],
    }
    svc = WorkflowService(admin_session, org.id)
    wf = await svc.create_workflow(name="Poison", entity_definition_id=definition.id, description=None)
    version = await svc.save_draft(wf.id, good_def)
    await svc.publish(wf.id, version.id)
    from api.repositories.workflow import WorkflowRepository

    await WorkflowRepository(admin_session, org.id).update(wf, enabled=True)
    await admin_session.commit()

    # Seed two records WITHOUT emitting outbox events (a repo with no outbox), so the
    # only pending events are the two updates below — exactly one per record. Otherwise
    # the outbox-enabled create would also emit an event, giving two events for r1 and
    # poisoning both of them.
    seed_repo = DynamicEntityRepository(admin_session, org.id, definition, fields)
    r1 = await seed_repo.create({"title": "one"})
    r2 = await seed_repo.create({"title": "two"})
    await admin_session.commit()
    # Two update events (two records changing).
    await repo.update(uuid.UUID(str(r1["id"])), {"title": "one!"})
    await repo.update(uuid.UUID(str(r2["id"])), {"title": "two!"})
    await admin_session.commit()

    disp = WorkflowDispatchService(admin_session, public_base_url="http://x")
    original = disp._process_event
    poisoned_record = uuid.UUID(str(r1["id"]))

    async def _maybe_poison(event, **kwargs):  # noqa: ANN001, ANN003
        if event["record_id"] == poisoned_record:
            raise RuntimeError("boom")
        return await original(event, **kwargs)

    monkeypatch.setattr(disp, "_process_event", _maybe_poison)
    counters = await disp.process_pending()
    await admin_session.commit()

    assert counters["skipped"] == 1  # the poisoned event
    assert counters["runs"] >= 1  # the healthy event still ran
