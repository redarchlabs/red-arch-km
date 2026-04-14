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
        """Replace the membership's dimension assignments.

        Every ID must resolve to a row visible under the current RLS tenant
        scope. If an ID is missing (typo, stale client cache, cross-org
        attempt), raises UnknownDimensionError listing the offenders so the
        router can translate to a 400. Silently dropping them would let the
        client think the save succeeded when it partially didn't.
        """
        if region_ids is not None:
            result = await self._session.execute(select(Region).where(Region.id.in_(region_ids)))
            regions = list(result.scalars().all())
            _assert_all_found("region", region_ids, {r.id for r in regions})
            membership.regions = regions
        if department_ids is not None:
            result = await self._session.execute(
                select(Department).where(Department.id.in_(department_ids))
            )
            departments = list(result.scalars().all())
            _assert_all_found("department", department_ids, {d.id for d in departments})
            membership.departments = departments
        if role_ids is not None:
            result = await self._session.execute(select(Role).where(Role.id.in_(role_ids)))
            roles = list(result.scalars().all())
            _assert_all_found("role", role_ids, {r.id for r in roles})
            membership.roles = roles
        if group_ids is not None:
            result = await self._session.execute(select(Group).where(Group.id.in_(group_ids)))
            groups = list(result.scalars().all())
            _assert_all_found("group", group_ids, {g.id for g in groups})
            membership.groups = groups
        await self._session.flush()
        return membership


class UnknownDimensionError(ValueError):
    """Raised when an assigned dimension ID doesn't exist in the current tenant."""

    def __init__(self, kind: str, missing: list[uuid.UUID]) -> None:
        self.kind = kind
        self.missing = missing
        super().__init__(
            f"Unknown {kind} id(s): {', '.join(str(m) for m in missing)}"
        )


def _assert_all_found(
    kind: str, requested: list[uuid.UUID], found: set[uuid.UUID]
) -> None:
    missing = [rid for rid in requested if rid not in found]
    if missing:
        raise UnknownDimensionError(kind, missing)
