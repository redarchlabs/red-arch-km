"""Schemas for views: admin CRUD + the resolved render contract.

A view reuses the form element tree (``FormConfig``) — including ``form_ref``
widgets and workflow-run buttons — and renders through the same ``FormRenderer``.
Its render payload reuses ``FormRenderRead`` (with ``related``/``values`` empty
when the view is not entity-bound)."""

from __future__ import annotations

import uuid
from datetime import datetime

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
    # Anonymous-access state. The token itself is NEVER returned — only whether
    # sharing is on, what it is pinned to, and when it lapses. A link that has
    # been lost is rotated, not recovered.
    public_enabled_at: datetime | None = None
    public_record_id: uuid.UUID | None = None
    public_expires_at: datetime | None = None


class ViewShareRequest(BaseModel):
    """Turn anonymous access on for one view (or rotate its link)."""

    model_config = ConfigDict(extra="forbid")

    # The record an anonymous visitor sees. Required for an entity-bound view:
    # without it the page would have nothing to render, and allowing the visitor
    # to choose would turn the link into a record browser.
    record_id: uuid.UUID | None = None
    expires_at: datetime | None = None


class ViewShareCreated(BaseModel):
    """The raw token, shown EXACTLY once — only its hash is stored."""

    model_config = ConfigDict(extra="forbid")

    url: str
    token: str
    expires_at: datetime | None = None
    record_id: uuid.UUID | None = None
    # Element types on this view that need a login to fetch their own data, so the
    # operator is told at enable time rather than finding an empty panel later.
    unsupported_elements: list[str] = Field(default_factory=list)
