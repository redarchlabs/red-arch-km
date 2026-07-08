"""Integration tests for the agent's form + view designer tools.

Exercises AgentService._dispatch directly (the handlers the LLM invokes) against
a real PostgreSQL, proving the tools create/list/get/update/delete real forms and
views through their services on the v2 element tree, and enforce the org-admin
boundary the REST API uses.
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
    """An admin agent (no OrgContext ⇒ _is_admin() is True)."""
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
        {"name": "Customer", "fields": [{"name": "Email", "field_type": "text"}, {"name": "Name", "field_type": "text"}]},
    )


def _fields(config: dict) -> list[dict]:
    return [e for e in config["elements"] if e["type"] == "field"]


async def test_agent_form_lifecycle(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)

    created = await agent._dispatch(
        "create_form",
        {
            "name": "Customer Intake",
            "entity_slug": "customer",
            "description": "Sign up",
            "config": {
                "version": 2,
                "elements": [
                    {"type": "field", "slug": "email", "label": "Your email", "required": True},
                    {"type": "field", "slug": "name"},
                ],
            },
        },
    )
    assert created["created_form"]["slug"] == "customer_intake"
    assert created["created_form"]["entity"] == "customer"
    form_id = created["created_form"]["id"]

    listing = await agent._dispatch("list_forms", {})
    assert any(f["id"] == form_id and f["is_active"] for f in listing["forms"])

    got = await agent._dispatch("get_form", {"form_id": form_id})
    assert got["form"]["name"] == "Customer Intake"
    assert [f["slug"] for f in _fields(got["form"]["config"])] == ["email", "name"]
    assert _fields(got["form"]["config"])[0]["required"] is True

    # update_form replaces the whole tree and toggles active state.
    updated = await agent._dispatch(
        "update_form",
        {
            "form_id": form_id,
            "name": "Customer Signup",
            "is_active": False,
            "config": {"version": 2, "elements": [{"type": "field", "slug": "email"}]},
        },
    )
    assert updated["updated_form"]["name"] == "Customer Signup"
    assert updated["updated_form"]["is_active"] is False

    reread = await agent._dispatch("get_form", {"form_id": form_id})
    assert [f["slug"] for f in _fields(reread["form"]["config"])] == ["email"]
    assert reread["form"]["is_active"] is False

    # delete_form removes it.
    deleted = await agent._dispatch("delete_form", {"form_id": form_id})
    assert deleted == {"deleted_form": form_id}
    assert await agent._dispatch("list_forms", {}) == {"forms": []}


async def test_agent_form_with_table_from_incoming_relationship(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    """A 1:M child collection (incoming many_to_one) backs a `table` element."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)
    await agent._dispatch("create_entity", {"name": "Contact", "fields": [{"name": "Phone", "field_type": "text"}]})
    await agent._dispatch(
        "create_relationship",
        {"source_slug": "contact", "target_slug": "customer", "name": "Customer", "cardinality": "many_to_one"},
    )

    schema = await agent._dispatch("get_entity_schema", {"slug": "customer"})
    incoming = [r for r in schema["relationships"] if r["direction"] == "incoming"]
    assert len(incoming) == 1 and incoming[0]["related_entity_slug"] == "contact"

    created = await agent._dispatch(
        "create_form",
        {
            "name": "Customer With Contacts",
            "entity_slug": "customer",
            "config": {
                "version": 2,
                "elements": [
                    {"type": "field", "slug": "email"},
                    {
                        "type": "table",
                        "anchor_relationship_id": incoming[0]["id"],
                        "columns": [{"kind": "field", "slug": "phone"}],
                    },
                ],
            },
        },
    )
    form_id = created["created_form"]["id"]
    got = await agent._dispatch("get_form", {"form_id": form_id})
    tables = [e for e in got["form"]["config"]["elements"] if e["type"] == "table"]
    assert len(tables) == 1
    assert tables[0]["anchor_relationship_id"] == incoming[0]["id"]


async def test_agent_create_form_rejects_unknown_entity(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    result = await agent._dispatch("create_form", {"name": "Ghost", "entity_slug": "ghost"})
    assert result["error"] == "entity not found: 'ghost'"  # a recovery `hint` may also be present


async def test_agent_create_form_rejects_unknown_field(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)
    result = await agent._dispatch(
        "create_form",
        {
            "name": "Bad",
            "entity_slug": "customer",
            "config": {"version": 2, "elements": [{"type": "field", "slug": "does_not_exist"}]},
        },
    )
    assert "error" in result
    assert await agent._dispatch("list_forms", {}) == {"forms": []}  # nothing left behind


async def test_agent_view_lifecycle(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """A standalone view (no entity) built from label + button elements."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)

    created = await agent._dispatch(
        "create_view",
        {
            "name": "Dashboard",
            "config": {
                "version": 2,
                "elements": [
                    {"type": "label", "text": "Welcome", "variant": "heading"},
                    {"type": "button", "label": "Open", "action": {"kind": "link", "href": "/x"}},
                ],
            },
        },
    )
    view_id = created["created_view"]["id"]

    listing = await agent._dispatch("list_views", {})
    assert any(v["id"] == view_id for v in listing["views"])

    got = await agent._dispatch("get_view", {"view_id": view_id})
    assert {e["type"] for e in got["view"]["config"]["elements"]} == {"label", "button"}

    await agent._dispatch("update_view", {"view_id": view_id, "name": "Home", "is_active": False})
    reread = await agent._dispatch("get_view", {"view_id": view_id})
    assert reread["view"]["name"] == "Home" and reread["view"]["is_active"] is False

    deleted = await agent._dispatch("delete_view", {"view_id": view_id})
    assert deleted == {"deleted_view": view_id}


async def test_agent_standalone_view_rejects_entity_bound_element(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    result = await agent._dispatch(
        "create_view",
        {"name": "Bad", "config": {"version": 2, "elements": [{"type": "field", "slug": "x"}]}},
    )
    assert "error" in result  # field requires an entity-bound view


async def test_agent_create_form_returns_located_validation_error(
    engine: AsyncEngine, admin_session: AsyncSession
) -> None:
    """A malformed element yields a LOCATED error the model can repair from.

    Regression for the opaque "invalid arguments: Field required" that gave the
    LLM no idea which element/field was wrong, so it retried blindly to the step
    limit. The message must now name the path (element index + field).
    """
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)

    result = await agent._dispatch(
        "create_form",
        {
            "name": "Bad Tree",
            "entity_slug": "customer",
            "config": {"version": 2, "elements": [{"type": "field", "label": "no slug"}]},
        },
    )
    assert "error" in result
    assert "elements.0" in result["error"]  # located to the offending element
    assert "slug" in result["error"] and "required" in result["error"].lower()


async def test_agent_validate_form_layout_dry_run(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """validate_form_layout checks a tree without persisting anything."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)
    await _seed_customer_entity(agent)

    # Structural failure (missing required field) → located error, nothing saved.
    bad = await agent._dispatch(
        "validate_form_layout",
        {"config": {"version": 2, "elements": [{"type": "field"}]}},
    )
    assert bad["valid"] is False
    assert "slug" in str(bad["errors"])

    # Binding failure — field slug doesn't exist on the entity.
    unbound = await agent._dispatch(
        "validate_form_layout",
        {"entity_slug": "customer", "config": {"version": 2, "elements": [{"type": "field", "slug": "ghost"}]}},
    )
    assert unbound["valid"] is False

    # A valid tree against the entity passes.
    ok = await agent._dispatch(
        "validate_form_layout",
        {"entity_slug": "customer", "config": {"version": 2, "elements": [{"type": "field", "slug": "email"}]}},
    )
    assert ok["valid"] is True

    # Dry-run persists nothing.
    assert await agent._dispatch("list_forms", {}) == {"forms": []}


async def test_agent_describe_reference_tools(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """The on-demand reference tools expose the element + action vocabularies."""
    org = await _org(admin_session)
    agent = _agent(engine, org.id)

    elements = await agent._dispatch("describe_form_elements", {})
    assert {"field", "button", "table", "form_ref"}.issubset(elements["elements"].keys())

    actions = await agent._dispatch("describe_workflow_actions", {})
    assert "send_webhook" in actions["actions"]
    assert "url" in actions["actions"]["send_webhook"]["config"]


async def test_reference_tools_are_not_admin_gated(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    """Pure-reference describe tools work for non-admins (no tenant data)."""
    org = await _org(admin_session)
    member = _member_agent(engine, org.id)
    assert "elements" in await member._dispatch("describe_form_elements", {})
    assert "actions" in await member._dispatch("describe_workflow_actions", {})
    # ...but the mutating dry-run stays admin-only.
    gated = await member._dispatch("validate_form_layout", {"config": {"version": 2, "elements": []}})
    assert "organization-admin" in gated["error"]


async def test_form_and_view_tools_are_admin_only(engine: AsyncEngine, admin_session: AsyncSession) -> None:
    org = await _org(admin_session)
    await _seed_customer_entity(_agent(engine, org.id))

    member = _member_agent(engine, org.id)
    for tool, args in (
        ("list_forms", {}),
        ("create_form", {"name": "X", "entity_slug": "customer"}),
        ("update_form", {"form_id": str(uuid.uuid4()), "name": "Y"}),
        ("delete_form", {"form_id": str(uuid.uuid4())}),
        ("list_views", {}),
        ("create_view", {"name": "V"}),
        ("update_view", {"view_id": str(uuid.uuid4()), "name": "Z"}),
        ("delete_view", {"view_id": str(uuid.uuid4())}),
    ):
        result = await member._dispatch(tool, args)
        assert "organization-admin" in result["error"]
