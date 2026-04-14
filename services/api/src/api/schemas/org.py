"""Organization schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    use_knowledge_graph: bool = True


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    use_knowledge_graph: bool | None = None


class OrgRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    use_knowledge_graph: bool


class DimensionCreate(BaseModel):
    """Shared schema for Region, Department, Role, Group creation."""
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class DimensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    permission_number: int
