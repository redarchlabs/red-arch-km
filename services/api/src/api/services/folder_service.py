"""Folder business logic: dot_path building, permission mask calculation."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import Folder
from api.repositories.folder import FolderRepository
from api.services.permission_config import permission_config_to_masks


async def build_dot_path(
    session: AsyncSession, name: str, parent_id: uuid.UUID | None
) -> str:
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
