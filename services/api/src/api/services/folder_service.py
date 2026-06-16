"""Folder business logic: dot_path building, permission mask calculation."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Folder
from api.repositories.folder import FolderRepository
from api.services.permission_config import permission_config_to_masks


async def build_dot_path(session: AsyncSession, name: str, parent_id: uuid.UUID | None) -> str:
    """Build a dot-separated path for a folder based on its ancestors."""
    if parent_id is None:
        return name

    repo = FolderRepository(session)
    parent = await repo.get(parent_id)
    if parent is None:
        return name
    return f"{parent.dot_path}.{name}"


async def compute_folder_masks(
    session: AsyncSession,
    org_id: uuid.UUID,
    viewer_config: list[dict] | None,
    contributor_config: list[dict] | None,
) -> tuple[list[int], list[int]]:
    """Resolve viewer and contributor permission configs to mask lists."""
    viewer_masks = await permission_config_to_masks(session, org_id, viewer_config)
    contributor_masks = await permission_config_to_masks(session, org_id, contributor_config)
    return viewer_masks, contributor_masks


class FolderCycleError(ValueError):
    """Raised when a folder move would create a cycle."""


def would_create_cycle(folder: Folder, new_parent: Folder | None) -> bool:
    """Check if moving `folder` under `new_parent` would create a cycle.

    A cycle occurs when `new_parent` is the folder itself or a descendant
    of it — detectable via dot_path prefix match without extra queries.
    """
    if new_parent is None:
        return False
    if new_parent.id == folder.id:
        return True
    folder_prefix = folder.dot_path
    if not folder_prefix:
        return False
    return new_parent.dot_path == folder_prefix or new_parent.dot_path.startswith(f"{folder_prefix}.")


async def move_folder(
    session: AsyncSession,
    folder: Folder,
    new_parent_id: uuid.UUID | None,
) -> Folder:
    """Move `folder` under `new_parent_id`, rebuilding dot_paths for the subtree.

    Raises FolderCycleError if the move would create a cycle.
    """
    repo = FolderRepository(session)

    new_parent: Folder | None = None
    if new_parent_id is not None:
        new_parent = await repo.get(new_parent_id)
        if new_parent is None:
            msg = f"Parent folder {new_parent_id} not found"
            raise ValueError(msg)

    if would_create_cycle(folder, new_parent):
        msg = "Cannot move a folder under itself or one of its descendants"
        raise FolderCycleError(msg)

    return await repo.move(folder, new_parent)
