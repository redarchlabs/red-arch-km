"""Organization repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.org import Org
from api.models.user import UserOrgMembership

# All-zero UUID used as the "clear" sentinel on update (None means "no change").
_NIL_UUID = uuid.UUID(int=0)


class OrgRepository:
    """Org queries that span tenants (no RLS scoping)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, org_id: uuid.UUID) -> Org | None:
        return await self._session.get(Org, org_id)

    async def list_for_user(self, profile_id: uuid.UUID, *, offset: int = 0, limit: int = 200) -> tuple[list[Org], int]:
        """Return a page of orgs where the user has a membership, plus total."""
        base = (
            select(Org)
            .join(UserOrgMembership, UserOrgMembership.org_id == Org.id)
            .where(UserOrgMembership.profile_id == profile_id)
        )
        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self._session.execute(base.order_by(Org.name).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def admin_org_ids(self, profile_id: uuid.UUID) -> set[uuid.UUID]:
        """Return the org ids where the user has an org-admin membership."""
        result = await self._session.execute(
            select(UserOrgMembership.org_id).where(
                UserOrgMembership.profile_id == profile_id,
                UserOrgMembership.is_org_admin.is_(True),
            )
        )
        return set(result.scalars().all())

    async def list_all(self, *, offset: int = 0, limit: int = 200) -> tuple[list[Org], int]:
        """Return a page of all orgs (site admin only), plus total count."""
        total = (await self._session.execute(select(func.count()).select_from(Org))).scalar_one()
        result = await self._session.execute(select(Org).order_by(Org.name).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def delete(self, org_id: uuid.UUID) -> bool:
        """Delete an org. CASCADE on FKs removes owned rows across all tables."""
        org = await self.get(org_id)
        if org is None:
            return False
        await self._session.delete(org)
        await self._session.flush()
        return True

    async def create(
        self,
        *,
        name: str,
        description: str | None = None,
        use_knowledge_graph: bool = True,
    ) -> Org:
        # Assign next permission_number (org_mask bit index). Row-level lock
        # prevents two concurrent org creations from picking the same number.
        count_result = await self._session.execute(
            select(Org.permission_number).order_by(Org.permission_number.desc()).limit(1).with_for_update()
        )
        last = count_result.scalar_one_or_none()
        permission_number = (last or 0) + 1

        org = Org(
            name=name,
            description=description,
            use_knowledge_graph=use_knowledge_graph,
            permission_number=permission_number,
        )
        self._session.add(org)
        await self._session.flush()
        return org

    async def update(
        self,
        org: Org,
        *,
        name: str | None = None,
        description: str | None = None,
        use_knowledge_graph: bool | None = None,
        openai_api_key: str | None = None,
        home_view_id: uuid.UUID | None = None,
    ) -> Org:
        if name is not None:
            org.name = name
        if description is not None:
            org.description = description
        if use_knowledge_graph is not None:
            org.use_knowledge_graph = use_knowledge_graph
        if home_view_id is not None:
            # None = no change (handled above). The all-zero UUID sentinel clears
            # the home view; any other value sets it.
            org.home_view_id = None if home_view_id == _NIL_UUID else home_view_id
        if openai_api_key is not None:
            # Stored as-is: the caller (router) is responsible for encrypting the
            # value before it reaches here (services/crypto.py). An empty string
            # clears the key.
            org.openai_api_key = openai_api_key or None
        await self._session.flush()
        return org
