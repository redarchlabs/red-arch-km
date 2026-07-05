"""User and membership repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.org import Org
from api.models.user import UserOrgMembership, UserProfile


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: uuid.UUID) -> UserProfile | None:
        return await self._session.get(UserProfile, profile_id)

    async def get_by_auth_subject(self, sub: str) -> UserProfile | None:
        result = await self._session.execute(select(UserProfile).where(UserProfile.auth_subject == sub))
        return result.scalar_one_or_none()

    async def list_in_org(
        self, org_id: uuid.UUID, *, offset: int = 0, limit: int = 200
    ) -> tuple[list[UserProfile], int]:
        """Return a page of users with membership in the given org, plus total."""
        from sqlalchemy import func

        base = (
            select(UserProfile)
            .join(UserOrgMembership, UserOrgMembership.profile_id == UserProfile.id)
            .where(UserOrgMembership.org_id == org_id)
        )
        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self._session.execute(base.order_by(UserProfile.username).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def list_all(
        self, *, offset: int = 0, limit: int = 50, q: str | None = None
    ) -> tuple[list[UserProfile], int]:
        """Return a page of ALL users (site-admin console), plus total.

        Optional `q` filters by username or email (case-insensitive substring).
        """
        base = select(UserProfile)
        if q:
            # Escape LIKE metacharacters so q is always a literal substring
            # search ("%" must not match everything, "_" not any-char).
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            base = base.where(
                or_(
                    UserProfile.username.ilike(pattern, escape="\\"),
                    UserProfile.email.ilike(pattern, escape="\\"),
                )
            )
        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self._session.execute(base.order_by(UserProfile.username).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def count_active_site_admins(self) -> int:
        """Number of active site admins — the last one is protected from
        demotion/deactivation so the instance can't lock itself out."""
        result = await self._session.execute(
            select(func.count())
            .select_from(UserProfile)
            .where(UserProfile.is_site_admin.is_(True), UserProfile.is_active.is_(True))
        )
        return int(result.scalar_one())

    async def list_memberships_with_orgs(self, profile_id: uuid.UUID) -> list[tuple[UserOrgMembership, Org]]:
        """All org memberships of a user with their orgs (global console view).

        Uses the plain (non-tenant) session — same pattern as
        OrgRepository.list_for_user behind /users/me. INVARIANT: this only
        returns rows when the API's DB role bypasses RLS (superuser or
        BYPASSRLS); under a plain `app_user`-style role, FORCE RLS on
        user_org_memberships makes this fail closed to an empty list. See
        docs/DATABASE.md.
        """
        result = await self._session.execute(
            select(UserOrgMembership, Org)
            .join(Org, UserOrgMembership.org_id == Org.id)
            .where(UserOrgMembership.profile_id == profile_id)
            .order_by(Org.name)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def get_membership(self, profile_id: uuid.UUID, org_id: uuid.UUID) -> UserOrgMembership | None:
        result = await self._session.execute(
            select(UserOrgMembership)
            .where(
                UserOrgMembership.profile_id == profile_id,
                UserOrgMembership.org_id == org_id,
            )
            .options(
                selectinload(UserOrgMembership.regions),
                selectinload(UserOrgMembership.departments),
                selectinload(UserOrgMembership.roles),
                selectinload(UserOrgMembership.groups),
            )
        )
        return result.scalar_one_or_none()
