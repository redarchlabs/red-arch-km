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
ApiKeyAccessMode = Literal["org", "scoped"]

# Generous per-list bounds: the real limit for dimensions is the mask cap
# (MAX_ACCESS_KEYS), checked at mint; folders expand to subfolders anyway.
_MAX_DIMENSION_IDS = 64
_MAX_FOLDER_IDS = 256


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=32)
    # Optional absolute expiry; omit for a non-expiring key.
    expires_at: datetime | None = None
    # Optional dimension assignments: the key reads knowledge with exactly the
    # masks a member holding these would. Any non-empty list (or folder_ids) makes
    # the key "scoped"; all empty = an org-wide key. Not editable after minting.
    region_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_DIMENSION_IDS)
    role_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_DIMENSION_IDS)
    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_DIMENSION_IDS)
    department_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_DIMENSION_IDS)
    # Optional folders (with their subfolders) that NARROW what the key reads; they
    # never grant. With no dimensions, the key reads with the masks of a member
    # with no assignments.
    folder_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_FOLDER_IDS)


class NamedRef(BaseModel):
    """An assignment shown on a key: a dimension's name, or a folder's path."""

    id: uuid.UUID
    name: str


class ApiKeyRead(BaseModel):
    """Metadata for one key — never includes the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    status: ApiKeyStatus
    created_by_profile_id: uuid.UUID | None
    # "org" = org-wide knowledge visibility; "scoped" = the assignments below.
    access_mode: ApiKeyAccessMode = "org"
    regions: list[NamedRef] = Field(default_factory=list)
    roles: list[NamedRef] = Field(default_factory=list)
    groups: list[NamedRef] = Field(default_factory=list)
    departments: list[NamedRef] = Field(default_factory=list)
    folders: list[NamedRef] = Field(default_factory=list)
    # Set only for a scoped key with no assignments left: released because the
    # key was revoked/expired (harmless), or — for an active key — broken (refused).
    assignments_note: str | None = None
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
