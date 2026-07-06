"""Folder management routes with permission-mask filtering."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_access, require_org_admin
from api.dependencies import get_tenant_db
from api.models.document import Document, Folder
from api.models.org import Org
from api.repositories.document import DocumentRepository
from api.repositories.folder import FolderRepository
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.document import FolderCreate, FolderRead, FolderUpdate
from api.services.folder_service import (
    FolderCycleError,
    build_dot_path,
    compute_folder_masks,
    move_folder,
)
from api.services.permission_config import calculate_user_masks_from_membership
from api.tasks.ingest import dispatch_metadata_update

logger = logging.getLogger(__name__)
router = APIRouter()


def _perm_propagation_payloads(
    org_id: uuid.UUID, folder: Folder, docs: list[Document], access_keys: list[int]
) -> list[dict[str, Any]]:
    """Build brain-api metadata-update payloads for a folder's inheriting docs.

    Each non-overridden document's vector-store ``access_keys`` are refreshed to
    ``access_keys`` (the changed folder's new effective masks), keeping its
    retrieval entitlement in sync after a permission change. The ``folder:<id>``
    tag reflects the document's OWN folder, not the folder that changed.
    """
    folder_tag = f"folder:{folder.id}"
    return [
        {
            "tenant_id": str(org_id),
            "document_key": doc.document_key,
            "new_tags": [t.name for t in doc.tags] + [folder_tag],
            "new_access_keys": access_keys,
            "title": doc.title,
        }
        for doc in docs
    ]


def _is_under_boundary(folder: Folder, boundaries: list[Folder]) -> bool:
    """True if ``folder`` lies within any boundary folder's subtree (inclusive).

    A boundary is a descendant that defines its OWN viewer config, so it (and
    everything beneath it) inherits itself rather than the folder that changed.
    """
    return any(
        folder.dot_path == b.dot_path or folder.dot_path.startswith(f"{b.dot_path}.")
        for b in boundaries
    )


async def _collect_subtree_propagation(
    session: AsyncSession, org_id: uuid.UUID, changed: Folder
) -> list[dict[str, Any]]:
    """Payloads to re-scope every document that inherits ``changed`` recursively.

    Walks ``changed``'s subtree. Descendant folders that define their own viewer
    config are inheritance boundaries: they and their subtrees keep their own
    entitlement and are skipped. Every other folder in the subtree inherits
    ``changed``'s new effective masks, so its non-overridden documents (NULL
    viewer config) are re-scoped to them. Using ``effective_view_masks`` for the
    masks means clearing ``changed``'s config correctly falls back to ITS
    nearest configured ancestor.
    """
    folder_repo = FolderRepository(session, org_id)
    doc_repo = DocumentRepository(session, org_id)

    subtree = await folder_repo.descendants(changed)  # includes `changed` itself
    new_masks = await folder_repo.effective_view_masks(changed)
    boundaries = [
        f for f in subtree if f.id != changed.id and f.viewer_permissions_config is not None
    ]

    payloads: list[dict[str, Any]] = []
    for folder in subtree:
        if _is_under_boundary(folder, boundaries):
            continue
        docs = await doc_repo.list_inheriting_in_folder(folder.id)
        payloads.extend(_perm_propagation_payloads(org_id, folder, docs, new_masks))
    return payloads


@router.get("/", response_model=PaginatedResponse[FolderRead])
async def list_folders(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[FolderRead]:
    """List folders visible to the current user via permission masks."""
    repo = FolderRepository(session, ctx.org_id)

    if ctx.is_org_admin:
        folders, total = await repo.list_visible_to_masks(
            user_masks=None,
            offset=pagination.offset,
            limit=pagination.page_size,
        )
    else:
        org = await session.get(Org, ctx.org_id)
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
        user_masks = calculate_user_masks_from_membership(ctx.membership, org.permission_number)
        folders, total = await repo.list_visible_to_masks(
            user_masks=user_masks,
            offset=pagination.offset,
            limit=pagination.page_size,
        )

    return make_page([FolderRead.model_validate(f) for f in folders], total, pagination)


@router.post("/", response_model=FolderRead, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FolderRead:
    """Create a folder with computed dot_path and resolved permission masks."""
    repo = FolderRepository(session, ctx.org_id)

    dot_path = await build_dot_path(session, ctx.org_id, body.name, body.parent_id)
    view_masks, contrib_masks = await compute_folder_masks(
        session,
        ctx.org_id,
        body.viewer_permissions_config,
        body.contributor_permissions_config,
    )

    folder = await repo.create(
        name=body.name,
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
    repo = FolderRepository(session, ctx.org_id)
    folder = await repo.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")
    return FolderRead.model_validate(folder)


@router.patch("/{folder_id}", response_model=FolderRead)
async def update_folder(
    folder_id: uuid.UUID,
    body: FolderUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> FolderRead:
    """Rename and/or reparent a folder.

    Distinguishes between "parent_id omitted" (leave unchanged) and
    "parent_id: null" (move to root) via `model_fields_set`.
    """
    repo = FolderRepository(session, ctx.org_id)
    folder = await repo.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")

    if body.name is not None and body.name != folder.name:
        folder = await repo.rename(folder, body.name)

    if body.description is not None:
        folder.description = body.description
        await session.flush()

    # Recompute permission masks whenever either config is explicitly provided.
    # Resolve BOTH configs together so masks reflect the folder's current stored
    # configs even when the caller sends only one of them.
    propagation_payloads: list[dict[str, Any]] = []
    if {"viewer_permissions_config", "contributor_permissions_config"} & body.model_fields_set:
        if "viewer_permissions_config" in body.model_fields_set:
            folder.viewer_permissions_config = body.viewer_permissions_config
        if "contributor_permissions_config" in body.model_fields_set:
            folder.contributor_permissions_config = body.contributor_permissions_config
        view_masks, contrib_masks = await compute_folder_masks(
            session,
            ctx.org_id,
            folder.viewer_permissions_config,
            folder.contributor_permissions_config,
        )
        folder.view_permission_masks = view_masks
        folder.contributor_permission_masks = contrib_masks
        await session.flush()

        # A viewer-permission change must propagate to every document that
        # inherits this folder — recursively through the subtree, stopping at
        # subfolders that define their OWN viewer config (they're their own
        # inheritance boundary). Overridden documents (NULL-config check inside)
        # are left untouched. Their brain-api ``access_keys`` were baked in at
        # ingest and would otherwise go stale. Only the viewer config affects
        # retrieval entitlement, so contributor-only changes don't propagate.
        # Gather now, while the RLS tenant context is active; dispatch after commit.
        if "viewer_permissions_config" in body.model_fields_set:
            propagation_payloads = await _collect_subtree_propagation(session, ctx.org_id, folder)

    if "parent_id" in body.model_fields_set:
        try:
            folder = await move_folder(session, ctx.org_id, folder, body.parent_id)
        except FolderCycleError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    # Commit before dispatching so a broker outage can't roll back the folder
    # change; the vector payloads are just briefly stale until a later touch.
    if propagation_payloads:
        await session.commit()
        for payload in propagation_payloads:
            try:
                dispatch_metadata_update(payload)
            except Exception:
                logger.exception(
                    "Failed to propagate folder %s permissions to document %s",
                    folder_id,
                    payload["document_key"],
                )

    logger.info("Updated folder %s in org %s", folder_id, ctx.org_id)
    return FolderRead.model_validate(folder)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    """Delete a folder. Refuses to delete folders that still have children.

    Documents under the folder have their `folder_id` set to NULL by the
    FK's ON DELETE SET NULL rule; callers should re-bucket them explicitly
    if that's not desired.
    """
    repo = FolderRepository(session, ctx.org_id)
    folder = await repo.get(folder_id)
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")

    # descendants() returns the folder itself + descendants, so >1 means children exist
    descendants = await repo.descendants(folder)
    if len(descendants) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete folder with children — move or delete them first",
        )

    await session.delete(folder)
    await session.flush()
    logger.info("Deleted folder %s in org %s", folder_id, ctx.org_id)
