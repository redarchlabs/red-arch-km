"""Repository for user-org memberships and dimension assignments."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.org import Department, Group, Region, Role
from api.models.user import UserOrgMembership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, membership_id: uuid.UUID) -> UserOrgMembership | None:
        result = await self._session.execute(
            select(UserOrgMembership)
            .where(UserOrgMembership.id == membership_id)
            .options(
                selectinload(UserOrgMembership.regions),
                selectinload(UserOrgMembership.departments),
                selectinload(UserOrgMembership.roles),
                selectinload(UserOrgMembership.groups),
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        profile_id: uuid.UUID,
        org_id: uuid.UUID,
        is_org_admin: bool = False,
    ) -> UserOrgMembership:
        existing = await self._session.execute(
            select(UserOrgMembership).where(
                UserOrgMembership.profile_id == profile_id,
                UserOrgMembership.org_id == org_id,
            )
        )
        membership = existing.scalar_one_or_none()

        if membership is None:
            membership = UserOrgMembership(
                profile_id=profile_id, org_id=org_id, is_org_admin=is_org_admin
            )
            self._session.add(membership)
        else:
            membership.is_org_admin = is_org_admin

        await self._session.flush()
        return membership

    async def assign_dimensions(
        self,
        membership: UserOrgMembership,
        *,
        region_ids: list[uuid.UUID] | None = None,
        department_ids: list[uuid.UUID] | None = None,
        role_ids: list[uuid.UUID] | None = None,
        group_ids: list[uuid.UUID] | None = None,
    ) -> UserOrgMembership:
        if region_ids is not None:
            result = await self._session.execute(select(Region).where(Region.id.in_(region_ids)))
            membership.regions = list(result.scalars().all())
        if department_ids is not None:
            result = await self._session.execute(
                select(Department).where(Department.id.in_(department_ids))
            )
            membership.departments = list(result.scalars().all())
        if role_ids is not None:
            result = await self._session.execute(select(Role).where(Role.id.in_(role_ids)))
            membership.roles = list(result.scalars().all())
        if group_ids is not None:
            result = await self._session.execute(select(Group).where(Group.id.in_(group_ids)))
            membership.groups = list(result.scalars().all())
        await self._session.flush()
        return membership
