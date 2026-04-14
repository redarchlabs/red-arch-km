"""Folder management routes with permission-mask filtering."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.dependencies import get_tenant_db
from api.models.org import Org
from api.repositories.folder import FolderRepository
from api.schemas.document import FolderCreate, FolderRead
from api.services.folder_service import build_dot_path, compute_folder_masks
from api.services.permission_config import calculate_user_masks_from_membership

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=list[FolderRead])
async def list_folders(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[FolderRead]:
    """List folders visible to the current user via permission masks."""
    repo = FolderRepository(session)

    if ctx.is_org_admin:
        folders = await repo.list_visible_to_masks(user_masks=None)
    else:
        org = await session.get(Org, ctx.org_id)
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
        user_masks = calculate_user_masks_from_membership(ctx.membership, org.permission_number)
        folders = await repo.list_visible_to_masks(user_masks=user_masks)

    return [FolderRead.model_validate(f) for f in folders]


@router.post("/", response_model=FolderRead, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FolderRead:
    """Create a folder with computed dot_path and resolved permission masks."""
    repo = FolderRepository(session)

    dot_path = await build_dot_path(session, body.name, body.parent_id)
    view_masks, contrib_masks = await compute_folder_masks(
        session,
        ctx.org_id,
        body.viewer_permissions_config,
        body.contributor_permissions_config,
    )

    folder = await repo.create(
        name=body.name,
        org_id=ctx.org_id,
        parent_id=body.parent_id,
        description=body.description,
        viewer_permissions_config=body.viewer_permissions_config,
        contributor_permissions_config=body.contributor_permissions_config,
        view_permission_masks=view_masks,
        contributor_permission_masks=contrib_masks,
        dot_path=dot_path,
    )
    logger.info("Created folder %s (%s) in org %s", folder.name, folder.id, ctx.org_id)
    return FolderRead.model_validate(folder)


@router.get("/{folder_id}", response_model=FolderRead)
async def get_folder(
    folder_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FolderRead:
    repo = FolderRepository(session)
    folder = await repo.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    return FolderRead.model_validate(folder)
