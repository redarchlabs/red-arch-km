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


class CurrentUserRead(BaseModel):
    """Current authenticated user with accessible orgs."""

    id: uuid.UUID
    username: str
    email: str
    is_site_admin: bool
    orgs: list[dict[str, str]]
