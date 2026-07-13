"""User schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str
    description: str | None
    is_site_admin: bool
    is_active: bool


class OrgSummary(BaseModel):
    """One accessible org plus whether the user administers it."""

    id: str
    name: str
    # True if the user is an org admin of this org (site admins: true for all).
    is_admin: bool
    # Optional per-org landing view; drives the sidebar "Home" nav item. Null = none.
    home_view_id: str | None = None


class CurrentUserRead(BaseModel):
    """Current authenticated user with accessible orgs."""

    id: uuid.UUID
    username: str
    email: str
    is_site_admin: bool
    orgs: list[OrgSummary]
