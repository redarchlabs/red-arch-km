"""Document attribute definition schemas."""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AttributeType = Literal["freeform", "picklist"]

_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class AttributeDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100)
    attribute_type: AttributeType = "freeform"
    picklist_options: list[str] = Field(default_factory=list)
    required: bool = False
    order: int = Field(default=0, ge=0)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not _SLUG_PATTERN.match(v):
            msg = "slug must start with a lowercase letter and contain only [a-z0-9_]"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _picklist_requires_options(self) -> AttributeDefinitionCreate:
        if self.attribute_type == "picklist" and not self.picklist_options:
            msg = "picklist attributes must define at least one option"
            raise ValueError(msg)
        if self.attribute_type == "freeform" and self.picklist_options:
            msg = "freeform attributes cannot have picklist_options"
            raise ValueError(msg)
        return self


class AttributeDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    attribute_type: AttributeType | None = None
    picklist_options: list[str] | None = None
    required: bool | None = None
    order: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _picklist_options_consistent(self) -> AttributeDefinitionUpdate:
        if self.attribute_type == "picklist" and self.picklist_options is not None and not self.picklist_options:
            msg = "picklist attributes must define at least one option"
            raise ValueError(msg)
        return self


class AttributeDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    attribute_type: str
    picklist_options: list[str]
    required: bool
    order: int
