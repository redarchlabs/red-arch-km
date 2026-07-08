"""Integration tests for the AI agent's tool layer (no OpenAI call needed).

Exercises AgentService._dispatch directly — the same handlers the LLM invokes —
to prove tools create real entities/records/workflows through the shared services.
"""

from __future__ import annotations

import uuid

import pytest
from api.config import Settings
from api.models.org import Org
from api.repositories.custom_entity import EntityDefinitionRepository
from api.services.agent import AgentService
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(secret_key="test")  # type: ignore[call-arg]


def _agent(engine: AsyncEngine, org_id: uuid.UUID) -> AgentService:
    """Build an AgentService against the test engine.

    Post Finding 2, AgentService opens a fresh RLS-scoped session per tool call
    from this factory (rather than borrowing a single request-scoped session),
    so tool writes commit independently — exactly the production behaviour this
    exercises. The separate ``admin_session`` then reads back the committed data.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return AgentService(org_id, _settings(), session_factory=factory)


async def _org(admin_session: AsyncSession) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=f"AGENT-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    return org


async def test_agent_creates_entity_and_workflow(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)

    # create_entity
    created = await agent._dispatch(
        "create_entity",
        {
            "name": "Customer",
            "fields": [
                {"name": "Email", "field_type": "text"},
                {"name": "Status", "field_type": "picklist", "picklist_options": ["active", "churned"]},
            ],
        },
    )
    assert created["created"]["slug"] == "customer"

    definition = await EntityDefinitionRepository(admin_session, org.id).get_by_slug("customer")
    assert definition is not None

    # list_entities sees it
    listing = await agent._dispatch("list_entities", {})
    assert any(e["slug"] == "customer" for e in listing["entities"])

    # add_entity_field
    added = await agent._dispatch(
        "add_entity_field", {"entity_slug": "customer", "name": "Phone", "field_type": "text"}
    )
    assert added["added_field"]["slug"] == "phone"

    # create_record
    rec = await agent._dispatch(
        "create_record", {"entity_slug": "customer", "values": {"email": "a@b.com", "status": "active"}}
    )
    assert "created_record_id" in rec

    # create_workflow (bare — trigger only, no actions)
    wf = await agent._dispatch("create_workflow", {"name": "Onboard", "entity_slug": "customer"})
    assert wf["created_workflow"]["name"] == "Onboard"
    assert wf["created_workflow"]["fires_on"] == "customer"


async def test_agent_wires_workflow_trigger_and_action(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    """create_workflow seeds a draft graph with the intended trigger operation
    and a create_record action targeting another entity."""
    from api.repositories.workflow import WorkflowRepository, WorkflowVersionRepository

    org = await _org(admin_session)
    agent = _agent(engine, org.id)

    await agent._dispatch("create_entity", {"name": "Customer", "fields": [{"name": "Email", "field_type": "text"}]})
    await agent._dispatch("create_entity", {"name": "Patient", "fields": [{"name": "Name", "field_type": "text"}]})

    result = await agent._dispatch(
        "create_workflow",
        {
            "name": "Create Patient on Customer Creation",
            "entity_slug": "customer",
            "operations": ["create"],
            "actions": [
                {"type": "create_record", "target_slug": "patient", "values": {"name": "New patient"}}
            ],
        },
    )
    assert result["trigger"]["operations"] == ["create"]
    assert result["actions"] == ["create_record"]
    assert result["draft_version"] == 1

    # The seeded draft version carries a real, executable graph.
    wf_id = uuid.UUID(result["created_workflow"]["id"])
    workflows = await WorkflowRepository(admin_session, org.id).list_all()
    assert any(w.id == wf_id for w in workflows)
    versions = await WorkflowVersionRepository(admin_session, org.id).list_for_workflow(wf_id)
    definition = versions[0].definition
    trigger = next(n for n in definition["nodes"] if n["type"] == "trigger")
    assert trigger["data"]["operations"] == ["create"]
    action = next(n for n in definition["nodes"] if n["type"] == "action")
    assert action["data"]["action_type"] == "create_record"
    assert action["data"]["config"] == {"target_slug": "patient", "values": {"name": "New patient"}}
    # trigger -> action edge exists so evaluate_graph reaches the action.
    assert any(e["source"] == "trigger" and e["target"] == action["id"] for e in definition["edges"])


async def test_agent_create_workflow_rejects_unknown_target(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await agent._dispatch("create_entity", {"name": "Customer", "fields": [{"name": "Email", "field_type": "text"}]})

    result = await agent._dispatch(
        "create_workflow",
        {
            "name": "Bad",
            "entity_slug": "customer",
            "actions": [{"type": "create_record", "target_slug": "ghost", "values": {}}],
        },
    )
    assert result["error"] == "target entity not found: 'ghost'"  # a recovery `hint` may also be present
    # No half-built workflow left behind.
    from api.repositories.workflow import WorkflowRepository

    assert await WorkflowRepository(admin_session, org.id).list_all() == []


async def test_agent_tool_errors_are_returned_not_raised(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)

    # Unknown entity → structured error, not an exception.
    result = await agent._dispatch("get_entity_schema", {"slug": "nonexistent"})
    assert result["error"] == "entity not found"  # a recovery `hint` may also be present

    # Unknown tool name → structured error.
    unknown = await agent._dispatch("do_something_weird", {})
    assert "error" in unknown
