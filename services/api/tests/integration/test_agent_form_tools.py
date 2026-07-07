"""Integration tests for the agent's intake-form tools.

Exercises AgentService._dispatch directly (the handlers the LLM invokes) against
a real PostgreSQL, proving the tools create/list/get/update real forms through
FormService and enforce the org-admin boundary the REST API uses.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from api.auth.dependencies import CurrentUser, OrgContext
from api.config import Settings
from api.models.org import Org
from api.services.agent import AgentService
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .helpers import set_tenant

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(secret_key="test")  # type: ignore[call-arg]


def _agent(engine: AsyncEngine, org_id: uuid.UUID) -> AgentService:
    """An admin agent (no OrgContext ⇒ _is_admin() is True, matching the other
    config-tool tests)."""
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
    org = Org(name=f"AGENTFORM-{uuid.uuid4().hex[:8]}")
    admin_session.add(org)
    await admin_session.commit()
    await set_tenant(admin_session, str(org.id))
    return org


async def _seed_customer_entity(agent: AgentService) -> None:
    await agent._dispatch(
        "create_entity",
        {
            "name": "Customer",
            "fields": [
                {"name": "Email", "field_type": "text"},
                {"name": "Name", "field_type": "text"},
            ],
        },
    )


async def test_agent_form_lifecycle(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)

    # create_form binds a form to the entity, exposing chosen fields.
    created = await agent._dispatch(
        "create_form",
        {
            "name": "Customer Intake",
            "entity_slug": "customer",
            "description": "Sign up",
            "fields": [
                {"slug": "email", "label": "Your email", "required": True},
                {"slug": "name"},
            ],
        },
    )
    assert created["created_form"]["slug"] == "customer_intake"
    assert created["created_form"]["entity"] == "customer"
    form_id = created["created_form"]["id"]

    # list_forms sees it.
    listing = await agent._dispatch("list_forms", {})
    assert any(f["id"] == form_id and f["is_active"] for f in listing["forms"])

    # get_form returns the full layout.
    got = await agent._dispatch("get_form", {"form_id": form_id})
    assert got["form"]["name"] == "Customer Intake"
    assert [f["slug"] for f in got["form"]["config"]["fields"]] == ["email", "name"]
    assert got["form"]["config"]["fields"][0]["required"] is True

    # update_form replaces the layout and toggles active state.
    updated = await agent._dispatch(
        "update_form",
        {
            "form_id": form_id,
            "name": "Customer Signup",
            "is_active": False,
            "fields": [{"slug": "email"}],
        },
    )
    assert updated["updated_form"]["name"] == "Customer Signup"
    assert updated["updated_form"]["is_active"] is False

    reread = await agent._dispatch("get_form", {"form_id": form_id})
    assert [f["slug"] for f in reread["form"]["config"]["fields"]] == ["email"]
    assert reread["form"]["is_active"] is False


async def test_agent_form_with_relationship_section(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """A form can surface a related entity as a section, using the relationship_id
    the model discovers via get_entity_schema."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)
    await agent._dispatch("create_entity", {"name": "Contact", "fields": [{"name": "Phone", "field_type": "text"}]})
    await agent._dispatch(
        "create_relationship",
        {"source_slug": "customer", "target_slug": "contact", "name": "Contacts", "cardinality": "one_to_many"},
    )

    schema = await agent._dispatch("get_entity_schema", {"slug": "customer"})
    rel = next(r for r in schema["relationships"] if r["direction"] == "outgoing")
    assert rel["id"]  # exposed so sections are constructable
    assert rel["related_entity_slug"] == "contact"

    created = await agent._dispatch(
        "create_form",
        {
            "name": "Customer With Contacts",
            "entity_slug": "customer",
            "fields": [{"slug": "email"}],
            "sections": [
                {
                    "relationship_id": rel["id"],
                    "mode": "table",
                    "label": "Contacts",
                    "fields": [{"slug": "phone"}],
                }
            ],
        },
    )
    form_id = created["created_form"]["id"]
    got = await agent._dispatch("get_form", {"form_id": form_id})
    sections = got["form"]["config"]["sections"]
    assert len(sections) == 1
    assert sections[0]["relationship_id"] == rel["id"]
    assert sections[0]["mode"] == "table"


async def test_agent_form_incoming_relationship_section(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """An INCOMING relationship (another entity points at the root) is discoverable
    via get_entity_schema and can back a 1:M child "table" section."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)
    await agent._dispatch("create_entity", {"name": "Contact", "fields": [{"name": "Phone", "field_type": "text"}]})
    # contact -> customer (many contacts to one customer): incoming from customer's view.
    await agent._dispatch(
        "create_relationship",
        {"source_slug": "contact", "target_slug": "customer", "name": "Customer", "cardinality": "many_to_one"},
    )

    schema = await agent._dispatch("get_entity_schema", {"slug": "customer"})
    incoming = [r for r in schema["relationships"] if r["direction"] == "incoming"]
    assert len(incoming) == 1
    assert incoming[0]["related_entity_slug"] == "contact"

    created = await agent._dispatch(
        "create_form",
        {
            "name": "Customer With Contacts",
            "entity_slug": "customer",
            "fields": [{"slug": "email"}],
            "sections": [
                {
                    "relationship_id": incoming[0]["id"],
                    "mode": "table",
                    "label": "Contacts",
                    "fields": [{"slug": "phone"}],
                }
            ],
        },
    )
    form_id = created["created_form"]["id"]
    got = await agent._dispatch("get_form", {"form_id": form_id})
    assert got["form"]["config"]["sections"][0]["relationship_id"] == incoming[0]["id"]


async def test_agent_create_form_rejects_unknown_entity(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    result = await agent._dispatch("create_form", {"name": "Ghost", "entity_slug": "ghost"})
    assert result == {"error": "entity not found: 'ghost'"}


async def test_agent_create_form_rejects_unknown_field(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)
    result = await agent._dispatch(
        "create_form",
        {"name": "Bad", "entity_slug": "customer", "fields": [{"slug": "does_not_exist"}]},
    )
    assert "error" in result
    # No form was left behind.
    listing = await agent._dispatch("list_forms", {})
    assert listing["forms"] == []


async def test_form_tools_are_admin_only(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    await _seed_customer_entity(_agent(engine, org.id))

    member = _member_agent(engine, org.id)
    for tool, args in (
        ("list_forms", {}),
        ("create_form", {"name": "X", "entity_slug": "customer"}),
        ("update_form", {"form_id": str(uuid.uuid4()), "name": "Y"}),
    ):
        result = await member._dispatch(tool, args)
        assert "organization-admin" in result["error"]
