"""Custom-entity catalog + record schemas."""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Scalar field types (relationship fields are modeled separately).
FieldType = Literal[
    "text",
    "long_text",
    "integer",
    "bigint",
    "numeric",
    "boolean",
    "date",
    "timestamptz",
    "uuid",
    "json",
    "picklist",
]

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]
OnDelete = Literal["CASCADE", "SET NULL", "RESTRICT"]

# Who may write an entity's records via the record API / who may read a field.
WriteAccess = Literal["member", "workflow_only"]
ReadAccess = Literal["member", "server_only"]

_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# A to-one FK physically lives on the source table (one_to_one, many_to_one).
_TO_ONE = ("one_to_one", "many_to_one")


def _validate_slug(v: str) -> str:
    if not _SLUG_PATTERN.match(v):
        msg = "slug must start with a lowercase letter and contain only [a-z0-9_]"
        raise ValueError(msg)
    return v


# --------------------------------------------------------------------------- #
# Entity fields
# --------------------------------------------------------------------------- #
class EntityFieldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=63)
    field_type: FieldType
    picklist_options: list[str] = Field(default_factory=list)
    is_required: bool = False
    is_unique: bool = False
    default_value: Any | None = None
    order: int = Field(default=0, ge=0)
    # "server_only" hides this field's values from the record API for regular
    # members (only the workflow engine + org admins read it). Default is open.
    read_access: ReadAccess = "member"

    _slug = field_validator("slug")(_validate_slug)

    @model_validator(mode="after")
    def _picklist_requires_options(self) -> EntityFieldCreate:
        if self.field_type == "picklist" and not self.picklist_options:
            raise ValueError("picklist fields must define at least one option")
        if self.field_type != "picklist" and self.picklist_options:
            raise ValueError("picklist_options is only valid for picklist fields")
        return self


class EntityFieldUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    picklist_options: list[str] | None = None
    is_required: bool | None = None
    order: int | None = Field(default=None, ge=0)
    # Toggle a field into/out of "server_only" (hide its values from members).
    read_access: ReadAccess | None = None
    # field_type / is_unique / slug are not editable in place (see plan §1.6):
    # type changes are restricted, and renames go through a dedicated path.


class EntityFieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    field_type: str
    picklist_options: list[str] | None
    is_required: bool
    is_unique: bool
    default_value: Any | None
    order: int
    read_access: ReadAccess


# --------------------------------------------------------------------------- #
# Entity relationships
# --------------------------------------------------------------------------- #
class EntityRelationshipCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=63)
    cardinality: Cardinality
    target_definition_id: uuid.UUID
    on_delete: OnDelete = "SET NULL"
    is_required: bool = False

    _slug = field_validator("slug")(_validate_slug)

    @model_validator(mode="after")
    def _on_delete_consistent(self) -> EntityRelationshipCreate:
        # A required to-one FK cannot be SET NULL on target delete — the column
        # is NOT NULL, so SET NULL would violate the constraint.
        if self.is_required and self.on_delete == "SET NULL" and self.cardinality in _TO_ONE:
            raise ValueError("a required relationship cannot use on_delete=SET NULL")
        return self


class EntityRelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    cardinality: str
    on_delete: str
    is_required: bool
    source_definition_id: uuid.UUID
    target_definition_id: uuid.UUID


# --------------------------------------------------------------------------- #
# Entity definitions
# --------------------------------------------------------------------------- #
class EntityDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=63)
    description: str | None = Field(default=None, max_length=2000)
    fields: list[EntityFieldCreate] = Field(default_factory=list)
    # "workflow_only" blocks direct member writes (only the workflow engine + org
    # admins). Default is open to any member.
    write_access: WriteAccess = "member"

    _slug = field_validator("slug")(_validate_slug)

    @model_validator(mode="after")
    def _unique_field_slugs(self) -> EntityDefinitionCreate:
        slugs = [f.slug for f in self.fields]
        if len(slugs) != len(set(slugs)):
            raise ValueError("duplicate field slugs within an entity")
        return self


class EntityDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    write_access: WriteAccess | None = None


class EntityDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    is_active: bool
    write_access: WriteAccess
    fields: list[EntityFieldRead] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Dynamic record payloads (shape is described by the entity's fields)
# --------------------------------------------------------------------------- #
class EntityRecordCreate(BaseModel):
    """Free-form record body; keys are field slugs, validated against the
    entity's catalog by the repository/service layer."""

    model_config = ConfigDict(extra="allow")

    data: dict[str, Any] = Field(default_factory=dict)


class EntityRecordUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    data: dict[str, Any] = Field(default_factory=dict)
