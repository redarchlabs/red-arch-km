"""Repository for MCP server connections — org-scoped, ciphertext-only.

Mirrors WorkflowConnectionRepository: every query filters by ``org_id`` and the
repo never holds the encryption key (it stores/returns ``secret_encrypted`` only).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.mcp_server import McpServer


class McpServerRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[McpServer]:
        result = await self._session.execute(
            select(McpServer).where(McpServer.org_id == self._org_id).order_by(McpServer.name)
        )
        return list(result.scalars().all())

    async def get(self, server_id: uuid.UUID) -> McpServer | None:
        result = await self._session.execute(
            select(McpServer).where(McpServer.id == server_id, McpServer.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> McpServer | None:
        result = await self._session.execute(
            select(McpServer).where(McpServer.name == name, McpServer.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, server: McpServer) -> McpServer:
        server.org_id = self._org_id
        self._session.add(server)
        await self._session.flush()
        return server

    async def flush(self) -> None:
        await self._session.flush()

    async def delete(self, server: McpServer) -> None:
        await self._session.delete(server)
        await self._session.flush()
