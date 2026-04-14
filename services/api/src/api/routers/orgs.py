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
from api.schemas.org import OrgCreate, OrgRead, OrgUpdate
from api.services.brain_client import BrainAPIClient

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=list[OrgRead])
async def list_orgs(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[OrgRead]:
    repo = OrgRepository(session)
    orgs = await repo.list_all() if user.is_site_admin else await repo.list_for_user(user.profile_id)
    return [OrgRead.model_validate(o) for o in orgs]


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
        user_orgs = await repo.list_for_user(user.profile_id)
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
