"""Schemas for views: admin CRUD + the resolved render contract.

A view reuses the form element tree (``FormConfig``) — including ``form_ref``
widgets and workflow-run buttons — and renders through the same ``FormRenderer``.
Its render payload reuses ``FormRenderRead`` (with ``related``/``values`` empty
when the view is not entity-bound)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.form import FormConfig


class ViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=63)
    description: str | None = None
    entity_definition_id: uuid.UUID | None = None
    config: FormConfig = Field(default_factory=FormConfig)


class ViewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    config: FormConfig | None = None
    is_active: bool | None = None


class ViewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    entity_definition_id: uuid.UUID | None
    config: FormConfig
    is_active: bool
