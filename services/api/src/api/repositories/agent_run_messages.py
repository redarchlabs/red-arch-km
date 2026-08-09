"""The steer mailbox — write one, and drain it exactly once.

The drain runs at the top of every turn of every run and almost always finds
nothing, so it is a single indexed statement against a partial index rather than a
read followed by a write.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent_run import AgentRunMessage


class AgentRunMessageRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def add(
        self, run_id: uuid.UUID, text: str, *, sent_by_profile_id: uuid.UUID | None = None
    ) -> AgentRunMessage:
        row = AgentRunMessage(run_id=run_id, text=text, sent_by_profile_id=sent_by_profile_id, org_id=self._org_id)
        self._session.add(row)
        await self._session.flush()
        return row

    async def drain(self, run_id: uuid.UUID) -> list[str]:
        """Take every undelivered steer for this run, oldest first.

        Marking delivered in the same statement that returns the rows is what makes
        this exactly-once: two drains racing the same message cannot both match it,
        because the second sees ``delivered_at`` already set. A read-then-write
        would deliver it twice, and the model would act on it twice.
        """
        rows = (
            await self._session.execute(
                update(AgentRunMessage)
                .where(
                    AgentRunMessage.run_id == run_id,
                    AgentRunMessage.org_id == self._org_id,
                    AgentRunMessage.delivered_at.is_(None),
                )
                .values(delivered_at=func.now())
                .returning(AgentRunMessage.text, AgentRunMessage.created_at)
            )
        ).all()
        return [r.text for r in sorted(rows, key=lambda r: r.created_at)]

    async def pending_count(self, run_id: uuid.UUID) -> int:
        """Undelivered steers, for telling someone their message is still queued."""
        rows = (
            await self._session.execute(
                select(AgentRunMessage.id).where(
                    AgentRunMessage.run_id == run_id,
                    AgentRunMessage.org_id == self._org_id,
                    AgentRunMessage.delivered_at.is_(None),
                )
            )
        ).all()
        return len(rows)
