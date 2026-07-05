"""Schemas for the global site-admin console endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class AdminUserUpdate(BaseModel):
    """Site-admin-editable user fields.

    Username/email are Clerk-owned (resynced on every login) so they are
    deliberately absent; extra="forbid" makes sending them a 422.
    """

    model_config = ConfigDict(extra="forbid")

    is_site_admin: bool | None = None
    is_active: bool | None = None


class UserMembershipSummary(BaseModel):
    """One org membership of a user, as seen from the global console."""

    model_config = ConfigDict(from_attributes=True)

    membership_id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    is_org_admin: bool


class ComponentStatus(BaseModel):
    status: str  # "ok" | "error"
    latency_ms: float | None = None
    detail: str | None = None


class SystemStatusRead(BaseModel):
    version: str
    components: dict[str, ComponentStatus]
