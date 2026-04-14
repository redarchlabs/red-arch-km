"""Membership schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class MembershipCreate(BaseModel):
    profile_id: uuid.UUID
    is_org_admin: bool = False
    region_ids: list[uuid.UUID] = Field(default_factory=list)
    department_ids: list[uuid.UUID] = Field(default_factory=list)
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    group_ids: list[uuid.UUID] = Field(default_factory=list)


class MembershipUpdate(BaseModel):
    is_org_admin: bool | None = None
    region_ids: list[uuid.UUID] | None = None
    department_ids: list[uuid.UUID] | None = None
    role_ids: list[uuid.UUID] | None = None
    group_ids: list[uuid.UUID] | None = None


class DimensionRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class MembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    org_id: uuid.UUID
    is_org_admin: bool
    regions: list[DimensionRef]
    departments: list[DimensionRef]
    roles: list[DimensionRef]
    groups: list[DimensionRef]
