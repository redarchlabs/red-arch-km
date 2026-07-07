"""Repository for views — org-scoped like the other tenant repos."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.view import View


class ViewRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[View]:
        result = await self._session.execute(
            select(View).where(View.org_id == self._org_id).order_by(View.name)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(View).where(View.org_id == self._org_id)
        )
        return int(result.scalar_one())

    async def get(self, view_id: uuid.UUID) -> View | None:
        result = await self._session.execute(
            select(View).where(View.id == view_id, View.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> View | None:
        result = await self._session.execute(
            select(View).where(View.slug == slug, View.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, view: View) -> View:
        view.org_id = self._org_id
        self._session.add(view)
        await self._session.flush()
        return view

    async def delete(self, view: View) -> None:
        await self._session.delete(view)
        await self._session.flush()
