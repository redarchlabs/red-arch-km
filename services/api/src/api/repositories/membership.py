"""Repository for user-org memberships and dimension assignments."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.org import Department, Group, Region, Role
from api.models.user import UserOrgMembership


class MembershipRepository:
    """Tenant-bound repository. Every query is explicitly scoped to ``org_id``
    (belt-and-suspenders alongside RLS)."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, membership_id: uuid.UUID) -> UserOrgMembership | None:
        result = await self._session.execute(
            select(UserOrgMembership)
            .where(
                UserOrgMembership.id == membership_id,
                UserOrgMembership.org_id == self._org_id,
            )
            .options(
                selectinload(UserOrgMembership.regions),
                selectinload(UserOrgMembership.departments),
                selectinload(UserOrgMembership.roles),
                selectinload(UserOrgMembership.groups),
            )
        )
        return result.scalar_one_or_none()

    async def count_org_admins(self) -> int:
        """Number of org-admin memberships in this repository's org."""
        from sqlalchemy import func

        result = await self._session.execute(
            select(func.count())
            .select_from(UserOrgMembership)
            .where(
                UserOrgMembership.org_id == self._org_id,
                UserOrgMembership.is_org_admin.is_(True),
            )
        )
        return int(result.scalar_one())

    async def delete(self, membership_id: uuid.UUID) -> bool:
        """Delete a membership in this org; returns False when it doesn't exist
        (or belongs to a different org)."""
        result = await self._session.execute(
            select(UserOrgMembership).where(
                UserOrgMembership.id == membership_id,
                UserOrgMembership.org_id == self._org_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            return False
        await self._session.delete(membership)
        await self._session.flush()
        return True

    async def upsert(
        self,
        *,
        profile_id: uuid.UUID,
        is_org_admin: bool = False,
    ) -> UserOrgMembership:
        existing = await self._session.execute(
            select(UserOrgMembership).where(
                UserOrgMembership.profile_id == profile_id,
                UserOrgMembership.org_id == self._org_id,
            )
        )
        membership = existing.scalar_one_or_none()

        if membership is None:
            membership = UserOrgMembership(profile_id=profile_id, org_id=self._org_id, is_org_admin=is_org_admin)
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
        # Assigning to a relationship collection requires SQLAlchemy to diff
        # against its current value. On an async session that means the
        # collection must already be loaded — lazy-loading here would throw
        # MissingGreenlet. Eagerly refresh only the collections we're about
        # to touch so we don't pay for ones we'll skip.
        to_refresh = [
            name
            for name, ids in (
                ("regions", region_ids),
                ("departments", department_ids),
                ("roles", role_ids),
                ("groups", group_ids),
            )
            if ids is not None
        ]
        if to_refresh:
            await self._session.refresh(membership, to_refresh)

        if region_ids is not None:
            region_result = await self._session.execute(
                select(Region).where(Region.id.in_(region_ids), Region.org_id == self._org_id)
            )
            regions = list(region_result.scalars().all())
            _assert_all_found("region", region_ids, {r.id for r in regions})
            membership.regions = regions
        if department_ids is not None:
            department_result = await self._session.execute(
                select(Department).where(Department.id.in_(department_ids), Department.org_id == self._org_id)
            )
            departments = list(department_result.scalars().all())
            _assert_all_found("department", department_ids, {d.id for d in departments})
            membership.departments = departments
        if role_ids is not None:
            role_result = await self._session.execute(
                select(Role).where(Role.id.in_(role_ids), Role.org_id == self._org_id)
            )
            roles = list(role_result.scalars().all())
            _assert_all_found("role", role_ids, {r.id for r in roles})
            membership.roles = roles
        if group_ids is not None:
            group_result = await self._session.execute(
                select(Group).where(Group.id.in_(group_ids), Group.org_id == self._org_id)
            )
            groups = list(group_result.scalars().all())
            _assert_all_found("group", group_ids, {g.id for g in groups})
            membership.groups = groups
        await self._session.flush()
        return membership


class UnknownDimensionError(ValueError):
    """Raised when an assigned dimension ID doesn't exist in the current tenant."""

    def __init__(self, kind: str, missing: list[uuid.UUID]) -> None:
        self.kind = kind
        self.missing = missing
        super().__init__(f"Unknown {kind} id(s): {', '.join(str(m) for m in missing)}")


def _assert_all_found(kind: str, requested: list[uuid.UUID], found: set[uuid.UUID]) -> None:
    missing = [rid for rid in requested if rid not in found]
    if missing:
        raise UnknownDimensionError(kind, missing)
