"""Document and folder schemas."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    text: str | None = None
    folder_id: uuid.UUID | None = None
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    use_knowledge_graph: bool | None = None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    document_key: str
    processing_status: str
    folder_id: uuid.UUID | None
    org_id: uuid.UUID
    created_at: Any


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    parent_id: uuid.UUID | None = None
    viewer_permissions_config: list[dict[str, Any]] | None = None
    contributor_permissions_config: list[dict[str, Any]] | None = None


class FolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    parent_id: uuid.UUID | None
    dot_path: str
    order: int
    org_id: uuid.UUID


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
