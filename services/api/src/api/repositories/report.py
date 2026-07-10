"""Repository for reports — org-scoped like the other tenant repos."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.report import Report


class ReportRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_all(self) -> list[Report]:
        result = await self._session.execute(
            select(Report).where(Report.org_id == self._org_id).order_by(Report.name)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Report).where(Report.org_id == self._org_id)
        )
        return int(result.scalar_one())

    async def get(self, report_id: uuid.UUID) -> Report | None:
        result = await self._session.execute(
            select(Report).where(Report.id == report_id, Report.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Report | None:
        result = await self._session.execute(
            select(Report).where(Report.slug == slug, Report.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

    async def create(self, report: Report) -> Report:
        report.org_id = self._org_id
        self._session.add(report)
        await self._session.flush()
        return report

    async def delete(self, report: Report) -> None:
        await self._session.delete(report)
        await self._session.flush()
