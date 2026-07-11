"""Repositories for the approvals + notifications inbox — org-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentApproval, AgentNotification


class AgentApprovalRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_pending(self) -> list[AgentApproval]:
        result = await self._session.execute(
            select(AgentApproval)
            .where(AgentApproval.org_id == self._org_id, AgentApproval.status == "pending")
            .order_by(AgentApproval.created_at)
        )
        return list(result.scalars().all())

    async def get(self, approval_id: uuid.UUID) -> AgentApproval | None:
        result = await self._session.execute(
            select(AgentApproval).where(
                AgentApproval.id == approval_id, AgentApproval.org_id == self._org_id
            )
        )
        return result.scalar_one_or_none()


class AgentNotificationRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self, *, unresolved_only: bool = False, limit: int = 100) -> list[AgentNotification]:
        stmt = select(AgentNotification).where(AgentNotification.org_id == self._org_id)
        if unresolved_only:
            stmt = stmt.where(AgentNotification.status != "resolved")
        stmt = stmt.order_by(AgentNotification.created_at.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, notification_id: uuid.UUID) -> AgentNotification | None:
        result = await self._session.execute(
            select(AgentNotification).where(
                AgentNotification.id == notification_id, AgentNotification.org_id == self._org_id
            )
        )
        return result.scalar_one_or_none()

    async def unread_count(self) -> int:
        from sqlalchemy import func

        result = await self._session.execute(
            select(func.count())
            .select_from(AgentNotification)
            .where(AgentNotification.org_id == self._org_id, AgentNotification.status == "unread")
        )
        return int(result.scalar_one())
