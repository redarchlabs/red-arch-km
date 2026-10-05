"""Folder repository with permission-mask filtering."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Folder

# The folder tree is walked by ``parent_id`` everywhere permissions, visibility or
# membership are resolved. ``dot_path`` is built from names, and two ROOT folders
# may share a name (``UniqueConstraint(org_id, name, parent_id)`` does not bind
# NULL parents), so it is a display string only. Bound for path rebuilds, guarding
# a corrupt cycle.
_MAX_DEPTH = 1000


def _effective_masks_by_parent(folder: Folder, by_id: dict[uuid.UUID, Folder]) -> list[int]:
    """Resolve a folder's effective view masks from an in-memory ``{id: folder}`` map.

    The bulk equivalent of ``FolderRepository.effective_view_masks`` (no
    per-folder query): own masks when the folder has its own viewer config, else
    the nearest configured ancestor's, else empty (public within the org).

    Walks ``parent_id``, never ``dot_path``: paths are built from names, and two
    root folders may share a name, so a name-keyed map can resolve the wrong
    ancestor. ``by_id`` must hold every ancestor of ``folder`` (a missing one ends
    the walk). The step bound guards a corrupt cycle.
    """
    node: Folder | None = folder
    for _ in range(len(by_id) + 1):
        if node is None:
            return []
        if node.viewer_permissions_config is not None:
            return list(node.view_permission_masks or [])
        node = by_id.get(node.parent_id) if node.parent_id is not None else None
    return []


def _is_visible(folder: Folder, by_id: dict[uuid.UUID, Folder], user_masks: set[int]) -> bool:
    """A folder is visible if it has no effective restriction, or one that
    overlaps the user's masks."""
    effective = _effective_masks_by_parent(folder, by_id)
    return not effective or bool(user_masks.intersection(effective))


class FolderRepository:
    """Tenant-bound repository. Every query is explicitly scoped to ``org_id``
    (belt-and-suspenders alongside RLS)."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, folder_id: uuid.UUID) -> Folder | None:
        result = await self._session.execute(
            select(Folder).where(Folder.id == folder_id, Folder.org_id == self._org_id)
        )
        return result.scalar_one_or_none()

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
          - have no EFFECTIVE view restrictions (public within the org), OR
          - have at least one effective mask overlapping the user's masks

        Visibility uses *effective* masks: a folder with no viewer config of its
        own inherits the nearest configured ancestor (mirroring how documents
        inherit their folder). So a folder under a restricted parent is hidden
        even if it defines no restriction itself. Resolution is done in Python
        over the org's full folder set — inheritance needs every ancestor
        anyway — walking ``parent_id`` (two root folders may share a name, and so
        a ``dot_path``). Folder counts per org are modest.

        Pagination is optional — when offset/limit are omitted, all matching
        rows are returned (used internally by document permission filtering).
        """
        all_folders = list(
            (await self._session.execute(select(Folder).where(Folder.org_id == self._org_id).order_by(Folder.dot_path)))
            .scalars()
            .all()
        )

        if user_masks is None:
            visible = all_folders  # admin view: no restriction
        else:
            by_id = {f.id: f for f in all_folders}
            user_set = set(user_masks)
            visible = [f for f in all_folders if _is_visible(f, by_id, user_set)]

        total = len(visible)
        if offset is not None:
            visible = visible[offset:]
        if limit is not None:
            visible = visible[:limit]
        return visible, total

    async def get_many(self, folder_ids: Collection[uuid.UUID]) -> list[Folder]:
        """These folders (those in this org), ordered by path."""
        if not folder_ids:
            return []
        result = await self._session.execute(
            select(Folder)
            .where(Folder.org_id == self._org_id, Folder.id.in_(list(folder_ids)))
            .order_by(Folder.dot_path, Folder.id)
        )
        return list(result.scalars().all())

    async def visible_subtrees(self, root_ids: Collection[uuid.UUID], user_masks: list[int]) -> dict[uuid.UUID, bool]:
        """The listed folders plus every descendant, each mapped to whether
        ``user_masks`` may see it.

        Expands by walking ``parent_id`` (a recursive CTE), never by ``dot_path``:
        paths are built from names and two ROOT folders may share a name, so a path
        prefix would pull a same-named root's whole subtree in. Only the subtree and
        the listed folders' ancestors (needed for inherited restrictions) are loaded
        — not every folder in the org. Ids not in this org are simply absent.
        """
        roots = list(dict.fromkeys(root_ids))
        if not roots:
            return {}
        subtree_ids = self._subtree_ids(roots, "key_subtree")
        ancestor_ids = self._ancestor_ids(roots, "key_ancestors")
        rows = (
            await self._session.execute(
                select(Folder, Folder.id.in_(subtree_ids).label("in_key_subtree")).where(
                    Folder.org_id == self._org_id, or_(Folder.id.in_(subtree_ids), Folder.id.in_(ancestor_ids))
                )
            )
        ).all()
        by_id = {folder.id: folder for folder, _ in rows}
        user_set = set(user_masks)
        return {folder.id: _is_visible(folder, by_id, user_set) for folder, in_subtree_flag in rows if in_subtree_flag}

    def _subtree_ids(self, root_ids: Collection[uuid.UUID], name: str = "folder_subtree") -> Select[tuple[uuid.UUID]]:
        """Ids of ``root_ids`` and every folder beneath them, walked by ``parent_id``
        (a recursive CTE; ``UNION`` dedupes, so a corrupt cycle still terminates).

        Never by ``dot_path``: paths are built from names and two ROOT folders may
        share a name, so a path prefix pulls a same-named root's subtree in.
        """
        in_org = Folder.org_id == self._org_id
        down = select(Folder.id).where(in_org, Folder.id.in_(list(root_ids))).cte(name, recursive=True)
        down = down.union(select(Folder.id).where(in_org, Folder.parent_id == down.c.id))
        return select(down.c.id)

    def _ancestor_ids(
        self, folder_ids: Collection[uuid.UUID], name: str = "folder_ancestors"
    ) -> Select[tuple[uuid.UUID]]:
        """Ids of ``folder_ids`` and every ancestor of them, walked by ``parent_id``."""
        in_org = Folder.org_id == self._org_id
        up = (
            select(Folder.id, Folder.parent_id).where(in_org, Folder.id.in_(list(folder_ids))).cte(name, recursive=True)
        )
        up = up.union(select(Folder.id, Folder.parent_id).where(in_org, Folder.id == up.c.parent_id))
        return select(up.c.id)

    async def ancestors(self, folder: Folder) -> list[Folder]:
        """``folder``'s ancestors, nearest first (itself excluded), by ``parent_id``.

        Loads the chain in one query and orders it in Python; the step bound
        guards a corrupt cycle.
        """
        if folder.parent_id is None:
            return []
        rows = await self._session.execute(
            select(Folder).where(Folder.org_id == self._org_id, Folder.id.in_(self._ancestor_ids([folder.parent_id])))
        )
        by_id = {f.id: f for f in rows.scalars()}
        chain: list[Folder] = []
        node = by_id.get(folder.parent_id)
        while node is not None and len(chain) <= len(by_id):
            chain.append(node)
            node = by_id.get(node.parent_id) if node.parent_id is not None else None
        return chain

    async def list_children(self, parent_id: uuid.UUID | None) -> list[Folder]:
        query = (
            select(Folder)
            .where(Folder.parent_id == parent_id, Folder.org_id == self._org_id)
            .order_by(Folder.order, Folder.name)
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
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
            org_id=self._org_id,
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

    async def nearest_configured_ancestor(self, folder: Folder) -> Folder | None:
        """The closest ancestor folder that has its OWN viewer config.

        Used to resolve inherited entitlement: a folder with a NULL viewer
        config inherits from the nearest ancestor that defines one. Returns
        ``None`` when no ancestor in the chain is configured.

        Walks ``parent_id``: two root folders may share a name (and so a
        ``dot_path``), and must not inherit each other's config.
        """
        return next((a for a in await self.ancestors(folder) if a.viewer_permissions_config is not None), None)

    async def effective_view_masks(self, folder: Folder | None) -> list[int]:
        """View masks a folder contributes to its documents, honoring inheritance.

        A folder with its OWN viewer config uses its own masks; a folder with a
        NULL config inherits the nearest configured ancestor's masks. Empty when
        nothing in the chain is configured (i.e. public within the org). This is
        the single source of truth for a document's inherited entitlement, so
        ingest-time derivation and folder-change propagation stay in lockstep.
        """
        if folder is None:
            return []
        if folder.viewer_permissions_config is not None:
            return list(folder.view_permission_masks or [])
        ancestor = await self.nearest_configured_ancestor(folder)
        return list(ancestor.view_permission_masks or []) if ancestor else []

    async def nearest_contributor_configured_ancestor(self, folder: Folder) -> Folder | None:
        """The closest ancestor folder with its OWN contributor config (or None)."""
        return next((a for a in await self.ancestors(folder) if a.contributor_permissions_config is not None), None)

    async def effective_contributor_masks(self, folder: Folder) -> list[int]:
        """Contributor masks governing who may ADD to a folder, honoring inheritance.

        Mirrors :meth:`effective_view_masks`: the folder's own contributor config
        if it has one, else the nearest configured ancestor's, else empty — which
        means "no contributor restriction beyond being able to see the folder".
        """
        if folder.contributor_permissions_config is not None:
            return list(folder.contributor_permission_masks or [])
        ancestor = await self.nearest_contributor_configured_ancestor(folder)
        return list(ancestor.contributor_permission_masks or []) if ancestor else []

    async def descendants(self, folder: Folder) -> list[Folder]:
        """Return this folder and all its descendants, walked by ``parent_id``."""
        result = await self._session.execute(
            select(Folder).where(Folder.org_id == self._org_id, Folder.id.in_(self._subtree_ids([folder.id])))
        )
        return list(result.scalars().all())

    async def rename(self, folder: Folder, new_name: str) -> Folder:
        """Rename a folder and rebuild dot_paths for the subtree."""
        parent = await self.get(folder.parent_id) if folder.parent_id else None
        new_prefix = f"{parent.dot_path}.{new_name}" if parent else new_name

        folder.name = new_name
        await self._session.flush()
        await self._rewrite_subtree_paths(new_prefix, folder.id)
        await self._session.refresh(folder)
        return folder

    async def move(self, folder: Folder, new_parent: Folder | None) -> Folder:
        """Reparent a folder and rebuild dot_paths for its subtree.

        Caller is responsible for validating that `new_parent` is not a
        descendant of `folder` (cycle prevention lives in the service layer).
        """
        new_prefix = f"{new_parent.dot_path}.{folder.name}" if new_parent else folder.name

        folder.parent_id = new_parent.id if new_parent else None
        await self._session.flush()
        await self._rewrite_subtree_paths(new_prefix, folder.id)
        await self._session.refresh(folder)
        return folder

    async def _rewrite_subtree_paths(self, new_prefix: str, folder_id: uuid.UUID) -> None:
        """Set ``folder_id``'s dot_path to ``new_prefix`` and rebuild every
        descendant's from its names, in one statement.

        The subtree is walked by ``parent_id`` — a ``LIKE 'old.%'`` match on the
        old path would also rewrite a same-named root's subtree (and, rebuilt
        from names, any path that drifted is repaired). The depth bound guards a
        corrupt cycle. Scoped to the repository's ``org_id`` so a rewrite can
        never touch another tenant's folders, whether or not RLS is enforced on
        the current connection.
        """
        await self._session.execute(
            sql_text(
                "WITH RECURSIVE subtree(id, path, depth) AS ("
                "  SELECT id, CAST(:new_prefix AS text), 0 FROM folders"
                "  WHERE id = :folder_id AND org_id = :org_id"
                "  UNION ALL"
                "  SELECT f.id, subtree.path || '.' || f.name, subtree.depth + 1"
                "  FROM folders f JOIN subtree ON f.parent_id = subtree.id"
                "  WHERE f.org_id = :org_id AND subtree.depth < :max_depth"
                ") "
                "UPDATE folders SET dot_path = subtree.path FROM subtree "
                "WHERE folders.id = subtree.id AND folders.org_id = :org_id"
            ),
            {"new_prefix": new_prefix, "folder_id": folder_id, "org_id": self._org_id, "max_depth": _MAX_DEPTH},
        )
        await self._session.flush()
