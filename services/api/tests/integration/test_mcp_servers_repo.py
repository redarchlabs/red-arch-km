"""Integration tests for MCP server storage: encryption at rest + RLS isolation."""

from __future__ import annotations

import uuid

import pytest
from api.models.mcp_server import McpServer
from api.models.org import Org
from api.repositories.mcp_server import McpServerRepository
from api.services.crypto import decrypt_secret, encrypt_secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.integration.helpers import set_tenant

pytestmark = pytest.mark.integration

_KEY = "dev-insecure-org-encryption-key-change-me"


async def _seed(admin_session: AsyncSession) -> tuple[Org, Org]:
    a = Org(name=f"McpA-{uuid.uuid4().hex[:8]}", permission_number=1)
    b = Org(name=f"McpB-{uuid.uuid4().hex[:8]}", permission_number=1)
    admin_session.add_all([a, b])
    await admin_session.commit()
    return a, b


async def test_secret_encrypted_at_rest(admin_session: AsyncSession) -> None:
    org_a, _ = await _seed(admin_session)
    server = McpServer(
        name="github", transport="http", url="https://mcp.example.com",
        secret_encrypted=encrypt_secret("s3cret", _KEY), org_id=org_a.id,
    )
    admin_session.add(server)
    await admin_session.commit()

    row = await McpServerRepository(admin_session, org_a.id).get_by_name("github")
    assert row.secret_encrypted != "s3cret"  # stored as ciphertext
    assert decrypt_secret(row.secret_encrypted, _KEY) == "s3cret"


async def test_rls_isolates_mcp_servers(session: AsyncSession, admin_session: AsyncSession) -> None:
    org_a, org_b = await _seed(admin_session)
    admin_session.add_all(
        [
            McpServer(name="a", transport="http", url="http://a", org_id=org_a.id),
            McpServer(name="b", transport="http", url="http://b", org_id=org_b.id),
        ]
    )
    await admin_session.commit()

    await set_tenant(session, str(org_a.id))
    visible = (await session.execute(select(McpServer.org_id))).scalars().all()
    assert set(visible) == {org_a.id}
