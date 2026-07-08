"""Integration tests for the agent's workflow lifecycle tools.

Exercises ``AgentService._dispatch`` directly (the handlers the LLM invokes)
against a real PostgreSQL, proving the assistant can AUTHOR (create / save a BPMN
graph / validate / publish), RUN (honoring run_permission), and DEBUG + MONITOR
(list runs, inspect steps+tokens, retry a failed run) real workflows — and that
the org-admin / run-permission boundaries are enforced exactly as the REST API's.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from api.auth.dependencies import CurrentUser, OrgContext
from api.config import Settings
from api.models.org import Org
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.repositories.dynamic_entity import DynamicEntityRepository
from api.repositories.workflow import (
    WorkflowRepository,
    WorkflowRunRepository,
    WorkflowTokenRepository,
    WorkflowVersionRepository,
)
from api.services.agent import AgentService
from api.services.workflow.engine import TokenEngine
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# harness (mirrors test_agent_form_tools.py)
# --------------------------------------------------------------------------- #
def _settings() -> Settings:
    return Settings(secret_key="test")  # type: ignore[call-arg]


def _agent(engine: AsyncEngine, org_id: uuid.UUID) -> AgentService:
    """Admin agent — no OrgContext ⇒ _is_admin() True, can_run() treated as admin."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return AgentService(org_id, _settings(), session_factory=factory)


def _member_ctx(org_id: uuid.UUID) -> OrgContext:
    user = CurrentUser(sub="member", username="member", email="m@x.com", profile_id=uuid.uuid4(), is_site_admin=False)
    membership = SimpleNamespace(regions=[], departments=[], roles=[], groups=[])
    return OrgContext(user=user, org_id=org_id, membership=membership, is_org_admin=False)


def _member_agent(engine: AsyncEngine, org_id: uuid.UUID) -> AgentService:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return AgentService(org_id, _settings(), session_factory=factory, org_context=_member_ctx(org_id))


async def _org(admin_session: AsyncSession) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=f"AGENTWF-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    return org


async def _seed_ticket_entity(agent: AgentService) -> None:
    await agent._dispatch(
        "create_entity",
        {
            "name": "Ticket",
            "fields": [
                {"name": "Title", "field_type": "text"},
                {"name": "Status", "field_type": "text"},
            ],
        },
    )


def _v2_update_graph(value: str = "DONE") -> dict:
    """A minimal, valid BPMN graph: start -> service task (set title) -> end."""
    return {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {"operations": ["update"]}},
            {
                "id": "t1",
                "type": "task",
                "data": {
                    "task_type": "service",
                    "action_type": "update_record_field",
                    "config": {"field": "title", "value": value},
                },
            },
            {"id": "done", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [
            {"source": "start", "target": "t1"},
            {"source": "t1", "target": "done"},
        ],
    }


async def _read_record(admin_session: AsyncSession, org_id: uuid.UUID, slug: str, record_id: uuid.UUID) -> dict:
    await set_tenant(admin_session, str(org_id))
    defn = await EntityDefinitionRepository(admin_session, org_id).get_by_slug(slug)
    assert defn is not None
    fields = await EntityFieldRepository(admin_session, org_id).list_for_definition(defn.id)
    rels = await EntityRelationshipRepository(admin_session, org_id).list_for_source(defn.id)
    repo = DynamicEntityRepository(admin_session, org_id, defn, fields, rels)
    record = await repo.get(record_id)
    assert record is not None
    return record


async def _new_ticket(agent: AgentService, *, title: str = "hello") -> str:
    rec = await agent._dispatch(
        "create_record", {"entity_slug": "ticket", "values": {"title": title, "status": "open"}}
    )
    return rec["created_record_id"]


async def _author_published_workflow(agent: AgentService, *, value: str = "DONE") -> str:
    """create_workflow -> save v2 graph -> publish; returns the workflow id."""
    created = await agent._dispatch(
        "create_workflow", {"name": f"WF {value}", "entity_slug": "ticket"}
    )
    wf_id = created["created_workflow"]["id"]
    saved = await agent._dispatch(
        "save_workflow_definition", {"workflow_id": wf_id, "definition": _v2_update_graph(value)}
    )
    assert "saved_draft" in saved, saved
    published = await agent._dispatch("publish_workflow", {"workflow_id": wf_id})
    assert "published" in published, published
    return wf_id


# --------------------------------------------------------------------------- #
# authoring
# --------------------------------------------------------------------------- #
async def test_agent_workflow_authoring_lifecycle(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)

    created = await agent._dispatch("create_workflow", {"name": "Close ticket", "entity_slug": "ticket"})
    wf_id = created["created_workflow"]["id"]

    # save_workflow_definition validates then stores a v2 draft.
    saved = await agent._dispatch(
        "save_workflow_definition", {"workflow_id": wf_id, "definition": _v2_update_graph()}
    )
    assert saved["saved_draft"]["workflow_id"] == wf_id
    draft_version_id = saved["saved_draft"]["version_id"]

    # validate_workflow on the saved graph reports it clean.
    valid = await agent._dispatch("validate_workflow", {"workflow_id": wf_id})
    assert valid["valid"] is True
    assert valid["errors"] == []

    # publish_workflow makes the latest draft live.
    published = await agent._dispatch("publish_workflow", {"workflow_id": wf_id})
    assert published["published"]["version_id"] == draft_version_id

    # get_workflow reflects the published version + returns the graph.
    got = await agent._dispatch("get_workflow", {"workflow_id": wf_id})
    wf = got["workflow"]
    assert wf["active_version_id"] == draft_version_id
    assert wf["entity"] == "ticket"
    assert [n["id"] for n in wf["definition"]["nodes"]] == ["start", "t1", "done"]
    published_versions = [v for v in wf["versions"] if v["status"] == "published"]
    assert [v["id"] for v in published_versions] == [draft_version_id]

    # list_workflows now surfaces the id + published state (so the model can chain).
    listing = await agent._dispatch("list_workflows", {})
    row = next(w for w in listing["workflows"] if w["id"] == wf_id)
    assert row["has_published_version"] is True


async def test_agent_save_definition_rejects_invalid_graph(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    created = await agent._dispatch("create_workflow", {"name": "Broken", "entity_slug": "ticket"})
    wf_id = created["created_workflow"]["id"]

    # A graph with no trigger is a hard error: nothing is saved.
    bad = {
        "schema_version": 2,
        "nodes": [
            {"id": "t1", "type": "task", "data": {"task_type": "service", "action_type": "log", "config": {}}},
            {"id": "done", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"source": "t1", "target": "done"}],
    }
    result = await agent._dispatch("save_workflow_definition", {"workflow_id": wf_id, "definition": bad})
    assert "error" in result
    assert any(i["code"] == "no-trigger" for i in result["issues"])

    # No draft was persisted (only the trigger-only v1 from create_workflow exists).
    got = await agent._dispatch("get_workflow", {"workflow_id": wf_id})
    assert all(v["status"] != "published" for v in got["workflow"]["versions"])
    assert len(got["workflow"]["versions"]) == 1


async def test_agent_validate_workflow_standalone_graph(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    # A definition passed inline is validated without touching the DB.
    dangling = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {"operations": ["update"]}},
            {"id": "island", "type": "task", "data": {"task_type": "service", "action_type": "log", "config": {}}},
        ],
        "edges": [],
    }
    result = await agent._dispatch("validate_workflow", {"definition": dangling})
    # Unreachable node is a warning (not an error) + missing end event warning.
    assert result["valid"] is True
    codes = {w["code"] for w in result["warnings"]}
    assert "unreachable" in codes


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #
async def test_agent_run_workflow_executes_real_side_effect(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    wf_id = await _author_published_workflow(agent, value="CLOSED")

    record_id = await _new_ticket(agent)

    run = await agent._dispatch("run_workflow", {"workflow_id": wf_id, "record_id": record_id, "operation": "update"})
    assert run["run"]["status"] == "succeeded", run
    assert run["run"]["actions_executed"] >= 1

    # The service task really mutated the record.
    fresh = await _read_record(admin_session, org.id, "ticket", uuid.UUID(record_id))
    assert fresh["title"] == "CLOSED"


async def test_agent_run_side_effecting_requires_record(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """A graph with an email/webhook/form step refuses to run on fabricated data."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    created = await agent._dispatch("create_workflow", {"name": "Notify", "entity_slug": "ticket"})
    wf_id = created["created_workflow"]["id"]
    graph = {
        "schema_version": 2,
        "nodes": [
            {"id": "start", "type": "trigger", "data": {"operations": ["update"]}},
            {
                "id": "mail",
                "type": "task",
                "data": {
                    "task_type": "send",
                    "action_type": "send_email",
                    "config": {"to": "a@b.com", "subject": "hi", "body": "x"},
                },
            },
            {"id": "done", "type": "event", "data": {"position": "end", "event_type": "none"}},
        ],
        "edges": [{"source": "start", "target": "mail"}, {"source": "mail", "target": "done"}],
    }
    await agent._dispatch("save_workflow_definition", {"workflow_id": wf_id, "definition": graph})
    await agent._dispatch("publish_workflow", {"workflow_id": wf_id})

    # No record_id → refuse (mirrors the REST manual-run guard).
    result = await agent._dispatch("run_workflow", {"workflow_id": wf_id, "operation": "update"})
    assert "error" in result
    assert "record_id" in result["error"]


async def test_agent_run_workflow_rejects_bad_operation(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    wf_id = await _author_published_workflow(agent)
    result = await agent._dispatch("run_workflow", {"workflow_id": wf_id, "operation": "frobnicate"})
    assert "error" in result
    assert "operation must be one of" in result["error"]


async def test_agent_test_workflow_is_side_effect_free(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """test_workflow simulates a (legacy) graph's actions without executing them."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    # create_workflow wires a v1 graph whose action the dry-run walker can simulate.
    created = await agent._dispatch(
        "create_workflow",
        {
            "name": "Log it",
            "entity_slug": "ticket",
            "operations": ["update"],
            "actions": [{"type": "log", "message": "dry"}],
        },
    )
    wf_id = created["created_workflow"]["id"]

    result = await agent._dispatch(
        "test_workflow",
        {"workflow_id": wf_id, "operation": "update", "before": {"title": "a"}, "after": {"title": "b"}},
    )
    tr = result["test_result"]
    assert tr["conditions_matched"] is True
    assert any(s["action_type"] == "log" for s in tr["steps"])
    # A dry run records NO run rows.
    runs = await agent._dispatch("list_workflow_runs", {"workflow_id": wf_id})
    assert runs["runs"] == []


# --------------------------------------------------------------------------- #
# monitoring + debugging
# --------------------------------------------------------------------------- #
async def test_agent_monitor_and_inspect_run(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    wf_id = await _author_published_workflow(agent)
    record_id = await _new_ticket(agent, title="x")
    run = await agent._dispatch(
        "run_workflow", {"workflow_id": wf_id, "record_id": record_id, "operation": "update"}
    )
    run_id = run["run"]["id"]

    # list_workflow_runs shows the run.
    listing = await agent._dispatch("list_workflow_runs", {"workflow_id": wf_id})
    assert any(r["id"] == run_id and r["status"] == "succeeded" for r in listing["runs"])

    # get_workflow_run returns steps + control-flow tokens.
    detail = await agent._dispatch("get_workflow_run", {"run_id": run_id})
    assert detail["run"]["status"] == "succeeded"
    assert any(s["node_id"] == "t1" and s["status"] == "succeeded" for s in detail["steps"])
    assert len(detail["tokens"]) >= 1


async def test_agent_get_workflow_run_unknown(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    result = await agent._dispatch("get_workflow_run", {"run_id": str(uuid.uuid4())})
    assert result["error"] == "run not found"  # a recovery `hint` may also be present


async def test_agent_retry_rejects_non_failed_run(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    wf_id = await _author_published_workflow(agent)
    record_id = await _new_ticket(agent, title="x")
    run = await agent._dispatch(
        "run_workflow", {"workflow_id": wf_id, "record_id": record_id, "operation": "update"}
    )
    # Succeeded run can't be retried.
    result = await agent._dispatch("retry_workflow_run", {"run_id": run["run"]["id"]})
    assert "error" in result
    assert "failed" in result["error"]


async def test_agent_retry_failed_run_completes(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """A crashed run (dead token, run=failed) is reactivated and driven to success
    by retry_workflow_run — reusing the token engine's re-drive path."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    wf_id = await _author_published_workflow(agent, value="RETRIED")
    record_id = uuid.UUID(await _new_ticket(agent))

    # Build a "crashed mid-run" state directly: an active token at the trigger,
    # killed to 'dead', with the run marked failed (as a reaper/crash would leave it).
    await set_tenant(admin_session, str(org.id))
    wf = await WorkflowRepository(admin_session, org.id).get(uuid.UUID(wf_id))
    assert wf is not None and wf.active_version_id is not None
    version = await WorkflowVersionRepository(admin_session, org.id).get(wf.active_version_id)
    assert version is not None
    run_repo = WorkflowRunRepository(admin_session, org.id)
    run = await run_repo.create_run_if_absent(
        workflow_id=wf.id,
        workflow_version_id=version.id,
        outbox_id=uuid.uuid4(),
        outbox_seq=None,
        created_at=func.now(),
        trigger_operation="update",
        record_id=record_id,
        input_snapshot={"before": {"title": "hello"}, "after": {"title": "hello"}},
        depth=0,
    )
    assert run is not None
    await TokenEngine(admin_session).start_run(run, version.definition)
    await WorkflowTokenRepository(admin_session, org.id).kill_all(run.id)
    await admin_session.execute(
        text("UPDATE workflow_runs SET status='failed', error='worker crashed' WHERE id=:id AND created_at=:ca"),
        {"id": run.id, "ca": run.created_at},
    )
    await admin_session.commit()

    # Retry via the agent tool: reactivates the dead token and re-drives to success.
    result = await agent._dispatch("retry_workflow_run", {"run_id": str(run.id)})
    assert result["retried_run"]["reactivated"] >= 1, result
    assert result["retried_run"]["status"] == "succeeded", result

    fresh = await _read_record(admin_session, org.id, "ticket", record_id)
    assert fresh["title"] == "RETRIED"


# --------------------------------------------------------------------------- #
# permission boundaries
# --------------------------------------------------------------------------- #
async def test_workflow_authoring_tools_are_admin_only(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    await _seed_ticket_entity(_agent(engine, org.id))
    member = _member_agent(engine, org.id)
    fake = str(uuid.uuid4())
    for tool, args in (
        # list_workflows mirrors GET /workflows/ (require_org_admin), so a member
        # is refused — the assistant must never do more than the UI allows.
        ("list_workflows", {}),
        ("get_workflow", {"workflow_id": fake}),
        ("update_workflow", {"workflow_id": fake, "name": "x"}),
        ("save_workflow_definition", {"workflow_id": fake, "definition": _v2_update_graph()}),
        ("publish_workflow", {"workflow_id": fake}),
        ("test_workflow", {"workflow_id": fake}),
        ("list_workflow_runs", {"workflow_id": fake}),
        ("get_workflow_run", {"run_id": fake}),
        ("retry_workflow_run", {"run_id": fake}),
    ):
        result = await member._dispatch(tool, args)
        assert "organization-admin" in result["error"], (tool, result)


async def test_run_workflow_honors_run_permission_not_admin(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """run_workflow is gated on the workflow's run_permission (default org_admin),
    NOT on the org-admin tool boundary — a member is refused by can_run, with a
    distinct message from the admin-tool refusal."""
    org = await _org(admin_session)
    admin = _agent(engine, org.id)
    await _seed_ticket_entity(admin)
    wf_id = await _author_published_workflow(admin)

    member = _member_agent(engine, org.id)
    result = await member._dispatch("run_workflow", {"workflow_id": wf_id, "operation": "update"})
    assert "error" in result
    assert "permission to run" in result["error"]
    assert "organization-admin" not in result["error"]

    # Widen run_permission to any_member → the same member may now run it.
    await admin._dispatch("update_workflow", {"workflow_id": wf_id, "run_permission": {"mode": "any_member"}})
    record_id = await _new_ticket(admin)
    ok = await member._dispatch(
        "run_workflow", {"workflow_id": wf_id, "record_id": record_id, "operation": "update"}
    )
    assert ok["run"]["status"] == "succeeded", ok


_APPROVAL_GRAPH = {
    "schema_version": 2,
    "nodes": [
        {"id": "start", "type": "trigger", "data": {"operations": ["update"]}},
        {"id": "approve", "type": "task", "data": {"task_type": "user", "label": "Approve"}},
        {"id": "gw", "type": "gateway",
         "data": {"gateway_type": "exclusive", "expr": {"var": "vars.approved"}}},
        {"id": "yes", "type": "task",
         "data": {"task_type": "service", "action_type": "log", "config": {"message": "approved"}}},
        {"id": "no", "type": "task",
         "data": {"task_type": "service", "action_type": "log", "config": {"message": "rejected"}}},
        {"id": "end", "type": "event", "data": {"position": "end", "event_type": "none"}},
    ],
    "edges": [
        {"id": "e0", "source": "start", "target": "approve"},
        {"id": "e1", "source": "approve", "target": "gw"},
        {"id": "e2", "source": "gw", "target": "yes", "source_handle": "true"},
        {"id": "e3", "source": "gw", "target": "no", "source_handle": "false"},
        {"id": "e4", "source": "yes", "target": "end"},
        {"id": "e5", "source": "no", "target": "end"},
    ],
}


async def test_agent_completes_a_waiting_human_task(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    created = await agent._dispatch("create_workflow", {"name": "Approval", "entity_slug": "ticket"})
    wf_id = created["created_workflow"]["id"]
    await agent._dispatch("save_workflow_definition", {"workflow_id": wf_id, "definition": _APPROVAL_GRAPH})
    await agent._dispatch("publish_workflow", {"workflow_id": wf_id})

    # Running it parks at the user task → run is waiting.
    run = await agent._dispatch("run_workflow", {"workflow_id": wf_id, "operation": "update"})
    assert run["run"]["status"] == "waiting", run
    run_id = run["run"]["id"]
    detail = await agent._dispatch("get_workflow_run", {"run_id": run_id})
    assert any(t["wait_kind"] == "user_task" and t["node_id"] == "approve" for t in detail["tokens"])

    # Completing it with an approval decision advances + routes the approved path.
    done = await agent._dispatch(
        "complete_workflow_task", {"run_id": run_id, "node_id": "approve", "variables": {"approved": True}}
    )
    assert done["completed_task"]["status"] == "succeeded", done
    after = await agent._dispatch("get_workflow_run", {"run_id": run_id})
    statuses = {s["node_id"]: s["status"] for s in after["steps"]}
    assert statuses.get("approve") == "succeeded"
    assert statuses.get("yes") == "succeeded"
    assert "no" not in statuses


async def test_agent_complete_task_rejects_non_waiting_run(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    result = await agent._dispatch("complete_workflow_task", {"run_id": str(uuid.uuid4())})
    assert result["error"] == "run not found"  # a recovery `hint` may also be present


async def test_agent_create_workflow_with_send_webhook(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """The simple builder now supports send_webhook — the exact step needed to
    'POST a value to a REST endpoint' — so the robot use case is ONE create_workflow
    call, not a hand-authored BPMN graph.
    """
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)

    created = await agent._dispatch(
        "create_workflow",
        {
            "name": "Post to robot",
            "entity_slug": "ticket",
            "operations": ["create"],
            "actions": [{"type": "send_webhook", "url": "https://robot.example.com/say", "body": {"channel": "voice"}}],
        },
    )
    assert "error" not in created, created
    assert created["actions"] == ["send_webhook"]
    # trigger + one action node.
    assert created["definition_summary"] == {"nodes": 2, "edges": 1}

    # The saved draft graph carries the webhook action + config (verifiable via get_workflow).
    got = await agent._dispatch("get_workflow", {"workflow_id": created["created_workflow"]["id"]})
    action_nodes = [n for n in got["workflow"]["definition"]["nodes"] if n["type"] == "action"]
    assert len(action_nodes) == 1
    assert action_nodes[0]["data"]["action_type"] == "send_webhook"
    assert action_nodes[0]["data"]["config"]["url"] == "https://robot.example.com/say"


async def test_agent_create_workflow_send_webhook_requires_url(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)
    result = await agent._dispatch(
        "create_workflow",
        {"name": "Bad", "entity_slug": "ticket", "actions": [{"type": "send_webhook"}]},
    )
    assert result == {"error": "send_webhook action requires url"}


async def test_agent_describe_bpmn_vocabulary(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """On-demand BPMN reference for full-graph authoring (ungated, no DB)."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    vocab = await agent._dispatch("describe_bpmn_vocabulary", {})
    assert {"trigger", "task", "gateway", "event"}.issubset(vocab["node_types"].keys())
    assert "schema_version" in vocab["graph_shape"]


async def test_agent_describe_workflow_actions_has_quick_pick(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    actions = await agent._dispatch("describe_workflow_actions", {})
    # The quick-picker maps intents → action type so the model doesn't guess.
    assert actions["quick_pick"]["POST to an external URL (simple)"] == "send_webhook"


async def test_agent_error_results_carry_recovery_hints(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """Failed tool calls attach a contextual `hint` (progressive disclosure —
    the recovery move rides the error, not the always-on system prompt)."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_ticket_entity(agent)

    # 'not found' → verify-with-a-listing-tool hint.
    nf = await agent._dispatch("create_workflow", {"name": "X", "entity_slug": "ghost"})
    assert "hint" in nf and "list_" in nf["hint"]

    # A form validation failure → dry-run-with-validate_form_layout hint.
    await agent._dispatch("create_entity", {"name": "Person", "fields": [{"name": "Email", "field_type": "text"}]})
    bad = await agent._dispatch(
        "create_form",
        {"name": "Bad", "entity_slug": "person", "config": {"version": 2, "elements": [{"type": "field"}]}},
    )
    assert "hint" in bad and "validate_form_layout" in bad["hint"]
