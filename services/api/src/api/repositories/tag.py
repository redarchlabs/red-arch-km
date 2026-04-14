"""Tag repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Tag


class TagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, tag_id: uuid.UUID) -> Tag | None:
        return await self._session.get(Tag, tag_id)

    async def list_all(self) -> list[Tag]:
        result = await self._session.execute(select(Tag).order_by(Tag.name))
        return list(result.scalars().all())

    async def create(self, *, name: str, org_id: uuid.UUID) -> Tag:
        tag = Tag(name=name, org_id=org_id)
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
