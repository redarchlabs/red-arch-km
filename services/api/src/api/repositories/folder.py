"""Folder repository with permission-mask filtering."""

from __future__ import annotations

import uuid

from sqlalchemy import bindparam, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Folder


class FolderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, folder_id: uuid.UUID) -> Folder | None:
        return await self._session.get(Folder, folder_id)

    async def list_visible_to_masks(
        self, user_masks: list[int] | None = None
    ) -> list[Folder]:
        """List folders visible to the given user masks.

        If user_masks is None, returns all folders (admin view).
        Otherwise returns folders that either:
          - have no view restrictions (public within the org), OR
          - have at least one mask overlapping the user's masks (PostgreSQL `&&`)
        """
        query = select(Folder)

        if user_masks is not None:
            masks_param = bindparam("user_masks", value=user_masks, type_=ARRAY(BIGINT))
            query = query.where(
                or_(
                    func.coalesce(func.array_length(Folder.view_permission_masks, 1), 0) == 0,
                    Folder.view_permission_masks.op("&&")(masks_param),
                )
            )

        query = query.order_by(Folder.dot_path)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def list_children(self, parent_id: uuid.UUID | None) -> list[Folder]:
        query = select(Folder).where(Folder.parent_id == parent_id).order_by(Folder.order, Folder.name)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        org_id: uuid.UUID,
        parent_id: uuid.UUID | None = None,
        description: str | None = None,
        viewer_permissions_config: list[dict] | None = None,
        contributor_permissions_config: list[dict] | None = None,
        view_permission_masks: list[int] | None = None,
        contributor_permission_masks: list[int] | None = None,
        dot_path: str = "",
    ) -> Folder:
        folder = Folder(
            name=name,
            org_id=org_id,
            parent_id=parent_id,
            description=description,
            viewer_permissions_config=viewer_permissions_config,
            contributor_permissions_config=contributor_permissions_config,
            view_permission_masks=view_permission_masks or [],
            contributor_permission_masks=contributor_permission_masks or [],
            dot_path=dot_path,
        )
        self._session.add(folder)
        await self._session.flush()
        return folder

    async def descendants(self, folder: Folder) -> list[Folder]:
        """Return this folder and all descendants via dot_path prefix match."""
        prefix = folder.dot_path
        result = await self._session.execute(
            select(Folder).where(
                (Folder.dot_path == prefix) | Folder.dot_path.like(f"{prefix}.%")
            )
        )
        return list(result.scalars().all())
