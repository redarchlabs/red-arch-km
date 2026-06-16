"""Routes for managing user memberships within the current org."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.repositories.membership import MembershipRepository, UnknownDimensionError
from api.schemas.membership import MembershipCreate, MembershipRead, MembershipUpdate

router = APIRouter()


@router.get("/by-user/{user_id}", response_model=MembershipRead | None)
async def get_user_membership(
    user_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MembershipRead | None:
    """Return a user's membership in the current org (or null if none exists).

    Used by the admin UI when opening the member-edit panel.
    """
    from api.repositories.user import UserRepository

    user_repo = UserRepository(session)
    membership = await user_repo.get_membership(user_id, ctx.org_id)
    if membership is None:
        return None
    return MembershipRead.model_validate(membership)


@router.post("/", response_model=MembershipRead, status_code=status.HTTP_201_CREATED)
async def create_membership(
    body: MembershipCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MembershipRead:
    repo = MembershipRepository(session)
    membership = await repo.upsert(profile_id=body.profile_id, org_id=ctx.org_id, is_org_admin=body.is_org_admin)
    try:
        membership = await repo.assign_dimensions(
            membership,
            region_ids=body.region_ids,
            department_ids=body.department_ids,
            role_ids=body.role_ids,
            group_ids=body.group_ids,
        )
    except UnknownDimensionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    # Reload with relationships
    reloaded = await repo.get(membership.id)
    if reloaded is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return MembershipRead.model_validate(reloaded)


@router.patch("/{membership_id}", response_model=MembershipRead)
async def update_membership(
    membership_id: uuid.UUID,
    body: MembershipUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> MembershipRead:
    repo = MembershipRepository(session)
    membership = await repo.get(membership_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if body.is_org_admin is not None:
        membership.is_org_admin = body.is_org_admin
    try:
        await repo.assign_dimensions(
            membership,
            region_ids=body.region_ids,
            department_ids=body.department_ids,
            role_ids=body.role_ids,
            group_ids=body.group_ids,
        )
    except UnknownDimensionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    reloaded = await repo.get(membership_id)
    if reloaded is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return MembershipRead.model_validate(reloaded)
