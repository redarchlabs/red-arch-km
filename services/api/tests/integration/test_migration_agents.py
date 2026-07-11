"""Integration test: agents + MCP servers export→import with id-remapping.

Verifies the agent-specific cross-references (supervisor self-ref, mcp_server_ids,
workflow_allowlist) are rewritten to the newly-created rows in the target org, and
that MCP secrets are never exported.
"""

from __future__ import annotations

import os
import uuid

import pytest
from api.models.agent import Agent
from api.models.mcp_server import McpServer
from api.models.org import Org
from api.repositories.agent import AgentRepository
from api.repositories.mcp_server import McpServerRepository
from api.repositories.workflow import WorkflowRepository
from api.services.crypto import encrypt_secret
from api.services.migration import CollisionStrategy, MigrationExporter, MigrationImporter
from api.services.workflow.service import WorkflowService
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.helpers import set_tenant

pytestmark = pytest.mark.integration

os.environ.setdefault("API_SECRET_KEY", "test-secret")


def _settings():
    from api.config import get_settings

    return get_settings()


async def _make_org(admin_session: AsyncSession, name: str) -> Org:
    await set_tenant(admin_session, None)
    org = Org(name=name)
    admin_session.add(org)
    await admin_session.commit()
    return org


async def test_agents_and_mcp_roundtrip_remaps_refs(admin_session: AsyncSession) -> None:
    source = await _make_org(admin_session, f"agsrc-{uuid.uuid4().hex[:8]}")
    target = await _make_org(admin_session, f"agtgt-{uuid.uuid4().hex[:8]}")

    await set_tenant(admin_session, str(source.id))
    wf = await WorkflowService(admin_session, source.id).create_workflow(
        name="Notify", entity_definition_id=None, description=None
    )
    key = _settings().org_encryption_key.get_secret_value()
    mcp = McpServer(
        name="github", transport="http", url="https://mcp.example.com",
        secret_encrypted=encrypt_secret("s3cret", key), org_id=source.id,
    )
    admin_session.add(mcp)
    await admin_session.flush()
    boss = Agent(name="boss", provider="openai", model="gpt-5-mini", kind="coordinator", org_id=source.id)
    admin_session.add(boss)
    await admin_session.flush()
    worker = Agent(
        name="worker", provider="openai", model="gpt-5-mini", supervisor_id=boss.id,
        mcp_server_ids=[str(mcp.id)], workflow_allowlist=[str(wf.id)],
        grants={"tools": ["run_workflow"]}, org_id=source.id,
    )
    admin_session.add(worker)
    await admin_session.commit()

    bundle = await MigrationExporter(admin_session, source.id).export()
    # Secrets are never exported.
    assert all("secret" not in s or s.get("secret") is None for s in bundle["resources"]["mcp_servers"])
    assert bundle["resources"]["mcp_servers"][0]["has_secret"] is True

    await set_tenant(admin_session, str(target.id))
    summary = await MigrationImporter(admin_session, target.id, _settings()).import_bundle(
        bundle, CollisionStrategy.SKIP
    )
    await admin_session.commit()
    assert any("secret" in w for w in summary.warnings)  # re-enter warning surfaced

    tgt_agents = {a.name: a for a in await AgentRepository(admin_session, target.id).list_all()}
    tgt_mcp = await McpServerRepository(admin_session, target.id).list_all()
    tgt_wf = {w.name: w for w in await WorkflowRepository(admin_session, target.id).list_all()}
    assert set(tgt_agents) == {"boss", "worker"}
    assert len(tgt_mcp) == 1 and tgt_mcp[0].secret_encrypted is None  # secret NOT imported

    new_boss, new_worker, new_mcp, new_wf = tgt_agents["boss"], tgt_agents["worker"], tgt_mcp[0], tgt_wf["Notify"]
    # Supervisor + mcp + workflow refs point at the NEW rows, not the source ids.
    assert new_worker.supervisor_id == new_boss.id
    assert new_worker.supervisor_id != boss.id
    assert new_worker.mcp_server_ids == [str(new_mcp.id)]
    assert new_worker.workflow_allowlist == [str(new_wf.id)]
