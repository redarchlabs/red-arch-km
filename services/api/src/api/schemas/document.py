"""Document and folder schemas."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _no_dots(name: str) -> str:
    if "." in name:
        msg = "Folder names cannot contain '.' (reserved for path separator)"
        raise ValueError(msg)
    return name


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    text: str | None = None
    folder_id: uuid.UUID | None = None
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    use_knowledge_graph: bool | None = None


class DocumentUpdate(BaseModel):
    """Partial update for a document. Only the provided fields are modified.

    `folder_id`, `tag_ids`, and `description` are tri-state (omitted / None /
    value); tests distinguish "omitted" from "explicit null" via
    `model_fields_set` to support clearing values.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    folder_id: uuid.UUID | None = None
    tag_ids: list[uuid.UUID] | None = None
    metadata: dict[str, Any] | None = None


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

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return _no_dots(v)


class FolderUpdate(BaseModel):
    """Partial update — any field may be omitted, including parent_id.

    Note: parent_id is a `str | None` tagged union here; a literal ``None``
    reparents to root, while omitting the field leaves the parent unchanged.
    We distinguish "unset" from "set to null" via the `model_fields_set` API.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    parent_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        return _no_dots(v) if v is not None else None


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
