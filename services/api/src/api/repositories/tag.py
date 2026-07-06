"""Tag repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Tag


class TagRepository:
    """Tenant-bound repository. Every query is explicitly scoped to ``org_id``
    (belt-and-suspenders alongside RLS)."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, tag_id: uuid.UUID) -> Tag | None:
        result = await self._session.execute(
            select(Tag).where(Tag.id == tag_id, Tag.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self, *, offset: int = 0, limit: int = 200) -> tuple[list[Tag], int]:
        """Return a page of tags along with the total count (org-scoped)."""
        base = select(Tag).where(Tag.org_id == self._org_id)
        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self._session.execute(base.order_by(Tag.name).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def create(self, *, name: str) -> Tag:
        tag = Tag(name=name, org_id=self._org_id)
        self._session.add(tag)
        await self._session.flush()
        return tag

    async def delete(self, tag_id: uuid.UUID) -> bool:
        tag = await self.get(tag_id)
        if tag is None:
            return False
        await self._session.delete(tag)
        await self._session.flush()
        return True
