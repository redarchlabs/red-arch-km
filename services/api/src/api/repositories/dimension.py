"""Shared repository for permission dimensions (Region, Department, Role, Group).

All four share the same shape: name, description, org-scoped uniqueness, and a
`permission_number` auto-assigned on creation.
"""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.org import Department, Group, Region, Role

DimensionModel = TypeVar("DimensionModel", Region, Department, Role, Group)


class DimensionRepository:
    """Generic repository for any permission dimension model."""

    def __init__(self, session: AsyncSession, model: type[DimensionModel]) -> None:
        self._session = session
        self._model = model

    async def get(self, dimension_id: uuid.UUID) -> DimensionModel | None:
        return await self._session.get(self._model, dimension_id)

    async def list_all(self, *, offset: int = 0, limit: int = 200) -> tuple[list[DimensionModel], int]:
        """Return a page of dimensions plus total count (RLS-scoped)."""
        from sqlalchemy import func

        total = (await self._session.execute(select(func.count()).select_from(self._model))).scalar_one()
        result = await self._session.execute(select(self._model).order_by(self._model.name).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def create(self, *, name: str, org_id: uuid.UUID, description: str | None = None) -> DimensionModel:
        # Assign next permission_number per-org. Row-level lock prevents the
        # select-max/insert race that would otherwise let two concurrent
        # creates pick the same value.
        last_num_result = await self._session.execute(
            select(self._model.permission_number)
            .where(self._model.org_id == org_id)
            .order_by(self._model.permission_number.desc())
            .limit(1)
            .with_for_update()
        )
        last_num = last_num_result.scalar_one_or_none() or 0

        instance = self._model(
            name=name,
            description=description,
            org_id=org_id,
            permission_number=last_num + 1,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def update(
        self,
        dimension_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> DimensionModel | None:
        instance = await self.get(dimension_id)
        if instance is None:
            return None
        if name is not None:
            instance.name = name
        if description is not None:
            instance.description = description
        await self._session.flush()
        return instance

    async def delete(self, dimension_id: uuid.UUID) -> bool:
        instance = await self.get(dimension_id)
        if instance is None:
            return False
        await self._session.delete(instance)
        await self._session.flush()
        return True
