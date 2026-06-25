"""Generic async CRUD repository."""

from __future__ import annotations

import uuid
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):  # noqa: UP046  # deferred: REDARCH-14 (PEP 695 generic rewrite)
    """Generic repository providing async CRUD operations.

    RLS policies on the database handle tenant scoping automatically
    when the session has `app.current_tenant_id` set.
    """

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def find_by_id(self, id: uuid.UUID) -> ModelT | None:
        return await self._session.get(self._model, id)

    async def find_all(self, *, offset: int = 0, limit: int = 20, **filters: Any) -> tuple[list[ModelT], int]:
        query = select(self._model)

        for key, value in filters.items():
            if hasattr(self._model, key) and value is not None:
                query = query.where(getattr(self._model, key) == value)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = (await self._session.execute(count_query)).scalar_one()

        # Paginate
        query = query.offset(offset).limit(limit)
        result = await self._session.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def create(self, **kwargs: Any) -> ModelT:
        instance = self._model(**kwargs)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def update(self, id: uuid.UUID, **kwargs: Any) -> ModelT | None:
        instance = await self.find_by_id(id)
        if instance is None:
            return None
        for key, value in kwargs.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        await self._session.flush()
        return instance

    async def delete(self, id: uuid.UUID) -> bool:
        instance = await self.find_by_id(id)
        if instance is None:
            return False
        await self._session.delete(instance)
        await self._session.flush()
        return True
