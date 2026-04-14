"""User management routes.

Users are auto-provisioned on first Keycloak login (see
services/user_provisioning.py). Only the current user may update their
profile, and user CREATE / DELETE flows through the identity provider.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import CurrentUser, OrgContext, get_current_user, require_org_access
from api.dependencies import get_db, get_tenant_db
from api.repositories.org import OrgRepository
from api.repositories.user import UserRepository
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.user import CurrentUserRead, UserRead

router = APIRouter()


class UserProfileUpdate(BaseModel):
    """Fields a user can update on their own profile."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=2000)


@router.get("/me", response_model=CurrentUserRead)
async def get_me(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUserRead:
    """Return the current user along with their accessible orgs."""
    org_repo = OrgRepository(session)
    if user.is_site_admin:
        orgs, _ = await org_repo.list_all(limit=10_000)
    else:
        orgs, _ = await org_repo.list_for_user(user.profile_id, limit=10_000)

    return CurrentUserRead(
        id=user.profile_id,
        username=user.username,
        email=user.email,
        is_site_admin=user.is_site_admin,
        orgs=[{"id": str(o.id), "name": o.name} for o in orgs],
    )


@router.patch("/me", response_model=UserRead)
async def update_me(
    body: UserProfileUpdate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> UserRead:
    """Update the current user's profile (description only).

    Username and email come from Keycloak and are overwritten on each
    login via `provision_user_from_claims`, so editing them here has no
    durable effect.
    """
    repo = UserRepository(session)
    profile = await repo.get(user.profile_id)
    if profile is None:
        msg = "Profile disappeared after provisioning"
        raise RuntimeError(msg)

    if body.description is not None:
        profile.description = body.description
    await session.flush()
    return UserRead.model_validate(profile)


@router.get("/", response_model=PaginatedResponse[UserRead])
async def list_users_in_org(
    ctx: Annotated[OrgContext, Depends(require_org_access)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[UserRead]:
    """List users with membership in the current org."""
    repo = UserRepository(session)
    users, total = await repo.list_in_org(
        ctx.org_id, offset=pagination.offset, limit=pagination.page_size
    )
    return make_page([UserRead.model_validate(u) for u in users], total, pagination)
