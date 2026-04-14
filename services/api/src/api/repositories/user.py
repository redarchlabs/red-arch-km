"""User and membership repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.user import UserOrgMembership, UserProfile


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: uuid.UUID) -> UserProfile | None:
        return await self._session.get(UserProfile, profile_id)

    async def get_by_keycloak_sub(self, sub: str) -> UserProfile | None:
        result = await self._session.execute(
            select(UserProfile).where(UserProfile.keycloak_sub == sub)
        )
        return result.scalar_one_or_none()

    async def list_in_org(self, org_id: uuid.UUID) -> list[UserProfile]:
        """Return users with a membership in the given org."""
        result = await self._session.execute(
            select(UserProfile)
            .join(UserOrgMembership, UserOrgMembership.profile_id == UserProfile.id)
            .where(UserOrgMembership.org_id == org_id)
            .order_by(UserProfile.username)
        )
        return list(result.scalars().all())

    async def get_membership(
        self, profile_id: uuid.UUID, org_id: uuid.UUID
    ) -> UserOrgMembership | None:
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
