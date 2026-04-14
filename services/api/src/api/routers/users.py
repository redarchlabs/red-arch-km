"""User management routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import CurrentUser, OrgContext, get_current_user, require_org_access
from api.dependencies import get_db, get_tenant_db
from api.repositories.org import OrgRepository
from api.repositories.user import UserRepository
from api.schemas.user import CurrentUserRead, UserRead

router = APIRouter()


@router.get("/me", response_model=CurrentUserRead)
async def get_me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUserRead:
    """Return the current user along with their accessible orgs."""
    org_repo = OrgRepository(session)
    orgs = await org_repo.list_all() if user.is_site_admin else await org_repo.list_for_user(user.profile_id)

    return CurrentUserRead(
        id=user.profile_id,
        username=user.username,
        email=user.email,
        is_site_admin=user.is_site_admin,
        orgs=[{"id": str(o.id), "name": o.name} for o in orgs],
    )


@router.get("/", response_model=list[UserRead])
async def list_users_in_org(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[UserRead]:
    """List users with membership in the current org."""
    repo = UserRepository(session)
    users = await repo.list_in_org(ctx.org_id)
    return [UserRead.model_validate(u) for u in users]
