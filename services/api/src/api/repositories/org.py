"""Organization repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.org import Org
from api.models.user import UserOrgMembership


class _Unset:
    """Sentinel distinguishing "field not passed" from an explicit ``None``,
    which for these nullable columns means "clear it"."""


UNSET = _Unset()


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
        default_llm_model: str | None = None,
    ) -> Org:
        if name is not None:
            org.name = name
        if description is not None:
            org.description = description
        if use_knowledge_graph is not None:
            org.use_knowledge_graph = use_knowledge_graph
        if openai_api_key is not None:
            # Stored as-is: the caller (router) is responsible for encrypting the
            # value before it reaches here (services/crypto.py). An empty string
            # clears the key.
            org.openai_api_key = openai_api_key or None
        if default_llm_model is not None:
            # Same convention as openai_api_key: empty string clears back to the
            # platform default, any other value pins the org to that model id.
            org.default_llm_model = default_llm_model.strip() or None
        await self._session.flush()
        return org

    async def set_home_view(self, org: Org, view_id: uuid.UUID | None) -> Org:
        """Set the org's landing view, or clear it with ``None``.

        Separate from :meth:`update` because it is written by a different
        caller with different privileges: the org-admin settings endpoint, not
        the site-admin org editor. Callers are responsible for checking that
        ``view_id`` belongs to this org (see routers/orgs.update_org_settings) —
        the column has no FK (see docs/DATABASE.md).
        """
        org.home_view_id = view_id
        await self._session.flush()
        return org

    async def set_branding(
        self,
        org: Org,
        *,
        accent_color: str | None | _Unset = UNSET,
        logo_object_key: str | None | _Unset = UNSET,
    ) -> Org:
        """Write the org-admin branding fields, leaving unpassed ones alone.

        ``UNSET`` (the default) means "no change" while an explicit ``None``
        clears — so the settings form can save the accent without also wiping a
        logo it wasn't editing.
        """
        if not isinstance(accent_color, _Unset):
            org.accent_color = accent_color
        if not isinstance(logo_object_key, _Unset):
            org.logo_object_key = logo_object_key
        await self._session.flush()
        return org
