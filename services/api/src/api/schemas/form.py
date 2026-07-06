"""Schemas for intake forms: admin CRUD, link generation, and the public form."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.services.email import is_valid_email

# How a related entity is surfaced on the form:
#   inline  — the 1:1 record's fields sit directly on the page
#   modal   — the 1:1 record's fields open in a popup ("Add details")
#   table   — a 1:M child collection edited as an add/remove-row table
SectionMode = Literal["inline", "modal", "table"]


class FormFieldConfig(BaseModel):
    """One entity field exposed on the form, with optional presentation overrides."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    label: str | None = None
    required: bool | None = None  # override the entity field's own requiredness
    help_text: str | None = None


class FormSectionConfig(BaseModel):
    """A related entity surfaced on the form (1:1 inline/modal, or 1:M table)."""

    model_config = ConfigDict(extra="forbid")

    relationship_id: uuid.UUID
    mode: SectionMode
    label: str | None = None
    fields: list[FormFieldConfig] = Field(default_factory=list)


class FormConfig(BaseModel):
    """The form's layout: root-entity fields plus related-entity sections."""

    model_config = ConfigDict(extra="forbid")

    fields: list[FormFieldConfig] = Field(default_factory=list)
    sections: list[FormSectionConfig] = Field(default_factory=list)


class FormCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=63)
    entity_definition_id: uuid.UUID
    description: str | None = None
    config: FormConfig = Field(default_factory=FormConfig)


class FormUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    config: FormConfig | None = None
    is_active: bool | None = None


class FormRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    entity_definition_id: uuid.UUID
    config: FormConfig
    is_active: bool


class GenerateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_record_id: uuid.UUID
    recipient_email: str | None = Field(default=None, max_length=320)
    expires_in_days: int | None = Field(default=14, ge=1, le=365)

    @field_validator("recipient_email")
    @classmethod
    def _validate_recipient_email(cls, value: str | None) -> str | None:
        """Reject a malformed recipient up front (clean 400 instead of a later
        smtplib failure / header-injection attempt via CR/LF)."""
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not is_valid_email(value):
            raise ValueError("recipient_email is not a valid email address")
        return value


class FormLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    form_id: uuid.UUID
    status: str
    recipient_email: str | None
    expires_at: datetime | None
    submitted_at: datetime | None


class FormLinkCreated(FormLinkRead):
    """Returned once at creation — carries the raw token + the shareable URL."""

    token: str
    url: str
    email_sent: bool = False


# ------------------------------------------------------------------ #
# Public (unauthenticated) form rendering + submission
# ------------------------------------------------------------------ #
class PublicFormField(BaseModel):
    """A field as the public page needs to render it (resolved from the catalog)."""

    slug: str
    label: str
    field_type: str
    required: bool
    help_text: str | None = None
    options: list[str] = Field(default_factory=list)  # picklist choices


class PublicFormSection(BaseModel):
    key: str  # relationship_id (the submission key under PublicFormSubmit.sections)
    label: str
    mode: SectionMode
    entity_name: str  # the related entity's display name (e.g. "Contact")
    fields: list[PublicFormField]
    rows: list[dict[str, Any]] = Field(default_factory=list)  # prefilled child rows (1:M)
    values: dict[str, Any] = Field(default_factory=dict)  # prefilled single record (1:1)


class PublicFormRead(BaseModel):
    """Everything the public page renders: schema + prefilled values, no auth."""

    form_name: str
    description: str | None
    fields: list[PublicFormField]
    values: dict[str, Any]  # prefilled root-record values (exposed fields only)
    sections: list[PublicFormSection] = Field(default_factory=list)
    status: str  # link status: pending | submitted | expired


class PublicFormSubmit(BaseModel):
    """A public submission: root field values plus per-section related data."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)
    # relationship_slug -> {values: {...}} (1:1) or {rows: [{...}, ...]} (1:M)
    sections: dict[str, Any] = Field(default_factory=dict)
