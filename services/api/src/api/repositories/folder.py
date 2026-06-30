"""Folder repository with permission-mask filtering."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import bindparam, func, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Folder

# PostgreSQL LIKE interprets % and _ as wildcards. dot_path is built from
# user-supplied folder names (see folder_service.build_dot_path), so a
# user who names a folder "foo_bar" or "a%" would otherwise match
# unrelated siblings during descendants() or rename subtree rewrites.
# Paired with the ESCAPE '\' clause on the LIKE pattern, this renders
# wildcards literal.
_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _escape_like(value: str) -> str:
    return value.translate(_LIKE_ESCAPE)


class FolderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, folder_id: uuid.UUID) -> Folder | None:
        return await self._session.get(Folder, folder_id)

    async def list_visible_to_masks(
        self,
        user_masks: list[int] | None = None,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[Folder], int]:
        """List folders visible to the given user masks, plus total count.

        If user_masks is None, returns all folders (admin view).
        Otherwise returns folders that either:
          - have no view restrictions (public within the org), OR
          - have at least one mask overlapping the user's masks (PostgreSQL `&&`)

        Pagination is optional — when offset/limit are omitted, all matching
        rows are returned (used internally by document permission filtering).
        """
        base = select(Folder)

        if user_masks is not None:
            masks_param = bindparam("user_masks", value=user_masks, type_=ARRAY(BIGINT))
            base = base.where(
                or_(
                    func.coalesce(func.array_length(Folder.view_permission_masks, 1), 0) == 0,
                    Folder.view_permission_masks.op("&&")(masks_param),
                )
            )

        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

        query = base.order_by(Folder.dot_path)
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        result = await self._session.execute(query)
        return list(result.scalars().all()), total

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
        viewer_permissions_config: list[dict[str, Any]] | None = None,
        contributor_permissions_config: list[dict[str, Any]] | None = None,
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
        escaped = _escape_like(prefix)
        result = await self._session.execute(
            select(Folder).where((Folder.dot_path == prefix) | Folder.dot_path.like(f"{escaped}.%", escape="\\"))
        )
        return list(result.scalars().all())

    async def rename(self, folder: Folder, new_name: str) -> Folder:
        """Rename a folder and rebuild dot_paths for the subtree."""
        old_prefix = folder.dot_path
        parent = await self.get(folder.parent_id) if folder.parent_id else None
        new_prefix = f"{parent.dot_path}.{new_name}" if parent else new_name

        folder.name = new_name
        await self._rewrite_subtree_paths(old_prefix, new_prefix, folder.id)
        await self._session.refresh(folder)
        return folder

    async def move(self, folder: Folder, new_parent: Folder | None) -> Folder:
        """Reparent a folder and rebuild dot_paths for its subtree.

        Caller is responsible for validating that `new_parent` is not a
        descendant of `folder` (cycle prevention lives in the service layer).
        """
        old_prefix = folder.dot_path
        new_prefix = f"{new_parent.dot_path}.{folder.name}" if new_parent else folder.name

        folder.parent_id = new_parent.id if new_parent else None
        await self._rewrite_subtree_paths(old_prefix, new_prefix, folder.id)
        await self._session.refresh(folder)
        return folder

    async def _rewrite_subtree_paths(self, old_prefix: str, new_prefix: str, folder_id: uuid.UUID) -> None:
        """Rewrite dot_path for the given folder and all descendants atomically.

        RLS still enforces tenant scope on these UPDATE statements; we rely
        on the `app.current_tenant_id` session setting being in effect.
        """
        await self._session.execute(
            sql_text("UPDATE folders SET dot_path = :new_prefix WHERE id = :folder_id"),
            {"new_prefix": new_prefix, "folder_id": folder_id},
        )
        await self._session.execute(
            sql_text(
                "UPDATE folders "
                "SET dot_path = :new_prefix || substr(dot_path, :old_len + 1) "
                "WHERE dot_path LIKE :old_like ESCAPE '\\'"
            ),
            {
                "new_prefix": new_prefix,
                "old_len": len(old_prefix),
                "old_like": f"{_escape_like(old_prefix)}.%",
            },
        )
        await self._session.flush()
