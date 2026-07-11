"""API-key schemas: management contract for the admin surface.

The plaintext key is present in exactly one response shape — :class:`ApiKeyCreated`,
returned once from ``POST /api/api-keys``. Every other read exposes only metadata
(the ``key_prefix``, scopes, timestamps) and never the secret.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApiKeyStatus = Literal["active", "revoked", "expired"]


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=32)
    # Optional absolute expiry; omit for a non-expiring key.
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    """Metadata for one key — never includes the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    status: ApiKeyStatus
    created_by_profile_id: uuid.UUID | None
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyRead):
    """The create response — carries the one-time plaintext ``key``."""

    key: str


class ScopeInfo(BaseModel):
    """One grantable scope + its description (drives the create form)."""

    name: str
    description: str
