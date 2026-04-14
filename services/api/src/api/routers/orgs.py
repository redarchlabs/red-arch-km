"""Organization CRUD routes."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_site_admin,
)
from api.config import Settings, get_settings
from api.dependencies import get_db
from api.repositories.org import OrgRepository
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.org import OrgCreate, OrgRead, OrgUpdate
from api.services.brain_client import BrainAPIClient

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=PaginatedResponse[OrgRead])
async def list_orgs(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[OrgRead]:
    repo = OrgRepository(session)
    if user.is_site_admin:
        orgs, total = await repo.list_all(
            offset=pagination.offset, limit=pagination.page_size
        )
    else:
        orgs, total = await repo.list_for_user(
            user.profile_id, offset=pagination.offset, limit=pagination.page_size
        )
    return make_page([OrgRead.model_validate(o) for o in orgs], total, pagination)


@router.post("/", response_model=OrgRead, status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OrgRead:
    repo = OrgRepository(session)
    org = await repo.create(
        name=body.name,
        description=body.description,
        use_knowledge_graph=body.use_knowledge_graph,
    )

    # Initialize tenant in vector/graph stores (best-effort, logged on failure).
    # Session commit happens in the get_db dependency on successful return.
    try:
        client = BrainAPIClient(settings)
        await client.init_tenant(str(org.id))
    except Exception as e:
        logger.error("Failed to initialize brain-api tenant for org %s: %s", org.id, e)

    return OrgRead.model_validate(org)


@router.get("/{org_id}", response_model=OrgRead)
async def get_org(
    org_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OrgRead:
    repo = OrgRepository(session)
    org = await repo.get(org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    # Visibility: site admin or member
    if not user.is_site_admin:
        user_orgs, _ = await repo.list_for_user(user.profile_id, limit=10_000)
        if org.id not in {o.id for o in user_orgs}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")

    return OrgRead.model_validate(org)


@router.patch("/{org_id}", response_model=OrgRead)
async def update_org(
    org_id: uuid.UUID,
    body: OrgUpdate,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> OrgRead:
    repo = OrgRepository(session)
    org = await repo.get(org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    org = await repo.update(
        org,
        name=body.name,
        description=body.description,
        use_knowledge_graph=body.use_knowledge_graph,
    )
    return OrgRead.model_validate(org)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    org_id: uuid.UUID,
    _admin: Annotated[CurrentUser, Depends(require_site_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete an org. Cascades to folders/documents/etc. via FK ON DELETE CASCADE.

    Site admin only. This is a destructive operation — it removes all data
    belonging to the org from PostgreSQL. Vector/graph cleanup in the brain-api
    is a separate concern (call /api/remove-document per doc before deleting
    the org if needed).
    """
    repo = OrgRepository(session)
    deleted = await repo.delete(org_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    logger.warning("Site-admin deleted org %s", org_id)
