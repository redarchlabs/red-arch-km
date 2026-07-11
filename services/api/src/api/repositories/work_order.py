"""Repository for work orders + tasks + diary entries — org-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.work_order import WorkOrder, WorkOrderEntry, WorkOrderTask


class WorkOrderRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[WorkOrder]:
        result = await self._session.execute(
            select(WorkOrder).where(WorkOrder.org_id == self._org_id).order_by(WorkOrder.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, work_order_id: uuid.UUID) -> WorkOrder | None:
        result = await self._session.execute(
            select(WorkOrder).where(WorkOrder.id == work_order_id, WorkOrder.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> WorkOrder | None:
        result = await self._session.execute(
            select(WorkOrder).where(WorkOrder.slug == slug, WorkOrder.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, wo: WorkOrder) -> WorkOrder:
        wo.org_id = self._org_id
        self._session.add(wo)
        await self._session.flush()
        return wo

    async def flush(self) -> None:
        await self._session.flush()

    # --- tasks -------------------------------------------------------------

    async def list_tasks(self, work_order_id: uuid.UUID) -> list[WorkOrderTask]:
        result = await self._session.execute(
            select(WorkOrderTask)
            .where(WorkOrderTask.work_order_id == work_order_id, WorkOrderTask.org_id == self._org_id)
            .order_by(WorkOrderTask.sort_order, WorkOrderTask.key)
        )
        return list(result.scalars().all())

    async def replace_tasks(self, work_order_id: uuid.UUID, tasks: list[WorkOrderTask]) -> list[WorkOrderTask]:
        existing = await self.list_tasks(work_order_id)
        for task in existing:
            await self._session.delete(task)
        for task in tasks:
            task.work_order_id = work_order_id
            task.org_id = self._org_id
            self._session.add(task)
        await self._session.flush()
        return tasks

    # --- diary -------------------------------------------------------------

    async def add_entry(self, entry: WorkOrderEntry) -> WorkOrderEntry:
        entry.org_id = self._org_id
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_entries(self, work_order_id: uuid.UUID, limit: int = 200) -> list[WorkOrderEntry]:
        result = await self._session.execute(
            select(WorkOrderEntry)
            .where(WorkOrderEntry.work_order_id == work_order_id, WorkOrderEntry.org_id == self._org_id)
            .order_by(WorkOrderEntry.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())
