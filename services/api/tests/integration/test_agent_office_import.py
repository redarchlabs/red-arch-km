"""Integration tests for the Agent Office import (real PostgreSQL).

The import is the bridge between the reference org's data and KM2's agent
platform, so what matters is not that rows appear but that they land in the shape
KM2's own governance reads: the kind-gate keys off ``kind``, the delegation
protocol keys off ``supervisor_id``, and the personas instruct agents to read a
charter that has to actually be searchable.
"""

from __future__ import annotations

import pytest
from api.models.agent import Agent
from api.models.agent_run import AgentSchedule
from api.models.document import Document
from api.services.agents.delegation import DELEGATE_TASK
from api.services.agents.kind_gate import kind_gate
from api.services.agents.office_import import KIND_MAP, import_into, load_roster
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _import(admin_session: AsyncSession, org_name: str) -> dict:
    result = await import_into(admin_session, org_name)
    await admin_session.flush()
    return result


async def _agents(admin_session: AsyncSession, org_id) -> dict[str, Agent]:
    rows = (await admin_session.execute(select(Agent).where(Agent.org_id == org_id))).scalars().all()
    return {a.name: a for a in rows}


class TestAgentOfficeImport:
    async def test_imports_the_whole_roster_with_personas(self, admin_session: AsyncSession) -> None:
        result = await _import(admin_session, "AO-roster")
        roster = load_roster()

        assert result["agents"] == len(roster)
        agents = await _agents(admin_session, result["org_id"])
        assert set(agents) == {e["name"] for e in roster}
        # The persona IS the system prompt — an agent imported without one is inert.
        assert all(a.persona for a in agents.values())
        pm = agents["program-manager"]
        assert "Program Manager" in (pm.persona or "")

    async def test_org_chart_is_wired_and_has_a_single_apex(self, admin_session: AsyncSession) -> None:
        result = await _import(admin_session, "AO-chart")
        agents = await _agents(admin_session, result["org_id"])

        apex = [a for a in agents.values() if a.supervisor_id is None]
        assert [a.name for a in apex] == ["program-manager"]
        # Reporting lines resolve to real agents in the same org.
        by_id = {a.id: a for a in agents.values()}
        assert agents["backend-engineer"].supervisor_id == agents["principal-engineer"].id
        assert agents["solution-architect"].supervisor_id == agents["technical-project-manager"].id
        for agent in agents.values():
            if agent.supervisor_id is not None:
                assert agent.supervisor_id in by_id

    async def test_every_agent_can_reach_the_apex_by_following_supervisors(self, admin_session: AsyncSession) -> None:
        """An orphaned subtree would silently never report to anyone."""
        result = await _import(admin_session, "AO-reachable")
        agents = await _agents(admin_session, result["org_id"])
        by_id = {a.id: a for a in agents.values()}

        for agent in agents.values():
            seen, node, hops = set(), agent, 0
            while node.supervisor_id is not None:
                assert node.id not in seen, f"cycle in the chain above {agent.name}"
                seen.add(node.id)
                node = by_id[node.supervisor_id]
                hops += 1
                assert hops < len(agents), f"{agent.name} never reaches an apex"
            assert node.name == "program-manager"

    async def test_kinds_map_onto_the_gate_that_enforces_them(self, admin_session: AsyncSession) -> None:
        """The reference's 'implementation' class is KM2's 'operator'; if that
        mapping is wrong the kind-gate silently mis-governs the whole roster."""
        result = await _import(admin_session, "AO-kinds")
        agents = await _agents(admin_session, result["org_id"])

        assert {a.kind for a in agents.values()} <= set(KIND_MAP.values())
        assert agents["backend-engineer"].kind == "operator"  # was "implementation"
        assert agents["program-manager"].kind == "coordinator"
        assert agents["security-analyst"].kind == "advisory"

        # And the gate actually bites: advisory agents cannot hand out work.
        for agent in agents.values():
            denied = kind_gate(agent.kind, DELEGATE_TASK)
            assert (denied is None) == (agent.kind in ("coordinator", "operator"))

    async def test_only_operators_may_write(self, admin_session: AsyncSession) -> None:
        result = await _import(admin_session, "AO-grants")
        agents = await _agents(admin_session, result["org_id"])

        for agent in agents.values():
            grants = agent.grants or {}
            assert grants.get("records_write", False) == (agent.kind == "operator")
            # Read tools are granted to everyone so a persona's "read the charter
            # first" instruction is actually executable.
            assert "search_knowledge" in grants.get("tools", [])

    async def test_charter_documents_are_searchable_knowledge(self, admin_session: AsyncSession) -> None:
        result = await _import(admin_session, "AO-docs")

        count = (
            await admin_session.execute(
                select(func.count()).select_from(Document).where(Document.org_id == result["org_id"])
            )
        ).scalar_one()
        assert count == result["documents"] >= 13

        charter = (
            await admin_session.execute(
                select(Document).where(
                    Document.org_id == result["org_id"],
                    Document.document_key == "agent-office/charter",
                )
            )
        ).scalar_one()
        assert charter.text and len(charter.text) > 1000
        assert charter.folder_id is not None

    async def test_schedules_import_disabled(self, admin_session: AsyncSession) -> None:
        """Importing a roster must never start firing unattended work by itself."""
        result = await _import(admin_session, "AO-sched")

        schedules = (
            (await admin_session.execute(select(AgentSchedule).where(AgentSchedule.org_id == result["org_id"])))
            .scalars()
            .all()
        )
        assert len(schedules) == result["schedules"] >= 1
        assert all(s.enabled is False for s in schedules)

    async def test_reimport_updates_in_place_rather_than_duplicating(self, admin_session: AsyncSession) -> None:
        first = await _import(admin_session, "AO-idempotent")
        agents = await _agents(admin_session, first["org_id"])
        pm_id = agents["program-manager"].id
        agents["program-manager"].persona = "STALE"
        await admin_session.flush()

        second = await _import(admin_session, "AO-idempotent")

        assert second["org_id"] == first["org_id"]
        assert second["agents"] == first["agents"]
        again = await _agents(admin_session, first["org_id"])
        assert len(again) == first["agents"]
        # Same row, refreshed content — ids are stable so runs//schedules keep pointing at it.
        assert again["program-manager"].id == pm_id
        assert again["program-manager"].persona != "STALE"
        # Documents and schedules do not double up either.
        assert second["documents"] == first["documents"]
        assert second["schedules"] == 0  # already present from the first import
