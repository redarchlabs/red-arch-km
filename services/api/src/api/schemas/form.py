"""Schemas for the flexible form designer: admin CRUD, link generation, and the
resolved render/submit contract shared by the public and authenticated surfaces.

The form's layout is a recursive **element tree** (``form_elements.py``). The
render contract deliberately sends the *authoring tree* plus a resolved **field
catalog** (entity field metadata) rather than a parallel "public tree": the one
``FormRenderer`` walks the authoring tree and looks up each leaf's type/options
from the catalog. This keeps a single source of truth for layout and lets the
builder preview reuse the exact same renderer with a catalog built from the
entity definition.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.schemas.form_elements import FormElement
from api.services.email import is_valid_email

# ------------------------------------------------------------------ #
# Legacy (pre-v2) layout upgrade
# ------------------------------------------------------------------ #
# v2 replaced the flat ``{fields, sections}`` layout with a ``{version, elements}``
# element tree, but shipped without migrating existing rows. ``FormConfig`` is
# ``extra="forbid"``, so a stale legacy row would 500 the list/render endpoints on
# ``extra_forbidden``. The before-validator below upgrades any such payload in place
# so old forms stay listable and renderable. A one-off backfill converts the stored
# rows; this shim is the safety net for anything the backfill misses.

# Optional presentational keys shared by v1 ``FormFieldConfig`` and v2 ``FieldElement``.
_LEGACY_FIELD_KEYS = ("label", "required", "help_text", "placeholder", "width", "display")


def _legacy_field_to_elements(field: dict[str, Any]) -> list[dict[str, Any]]:
    """Map one v1 field dict to v2 element dicts: a ``field``, optionally preceded
    by a ``label`` carrying the legacy ``heading`` (which v2 fields don't have)."""
    element: dict[str, Any] = {"type": "field", "slug": field["slug"]}
    for key in _LEGACY_FIELD_KEYS:
        value = field.get(key)
        if value is not None:
            element[key] = value
    elements: list[dict[str, Any]] = []
    heading = field.get("heading")
    if heading:
        elements.append({"type": "label", "text": heading, "variant": "subheading"})
    elements.append(element)
    return elements


def upgrade_legacy_form_config(data: Any) -> Any:
    """Upgrade a pre-v2 ``{fields, sections}`` layout to the v2 ``{version, elements}``
    tree. Passes through anything already in v2 shape (or not a dict)."""
    if not isinstance(data, dict) or "elements" in data:
        return data
    if "fields" not in data and "sections" not in data:
        return data

    elements: list[dict[str, Any]] = []
    for field in data.get("fields") or []:
        if isinstance(field, dict):
            elements.extend(_legacy_field_to_elements(field))
    for section in data.get("sections") or []:
        if not isinstance(section, dict):
            continue
        children: list[dict[str, Any]] = []
        for field in section.get("fields") or []:
            if isinstance(field, dict):
                children.extend(_legacy_field_to_elements(field))
        elements.append(
            {
                "type": "section",
                "relationship_id": section.get("relationship_id"),
                "mode": section.get("mode", "inline"),
                "label": section.get("label"),
                "elements": children,
            }
        )
    return {"version": 2, "elements": elements}


class FormConfig(BaseModel):
    """A form's layout: a versioned, recursive tree of typed elements."""

    model_config = ConfigDict(extra="forbid")

    version: int = 2
    elements: list[FormElement] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, data: Any) -> Any:
        return upgrade_legacy_form_config(data)


# ------------------------------------------------------------------ #
# Admin CRUD
# ------------------------------------------------------------------ #
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
# Resolved render contract (shared: public token page + authenticated fill)
# ------------------------------------------------------------------ #
class FieldMeta(BaseModel):
    """Resolved metadata for one entity field — the truth the renderer needs to
    pick a control and validate. Presentational overrides come from the element."""

    slug: str
    label: str  # the entity field's own name (element may override the display)
    field_type: str
    required: bool
    options: list[str] = Field(default_factory=list)  # picklist choices


class EntityCatalogEntry(BaseModel):
    """Field catalog for one entity referenced anywhere in the tree."""

    entity_id: uuid.UUID
    name: str
    fields: list[FieldMeta] = Field(default_factory=list)


class RelationshipMeta(BaseModel):
    """Where a section/table/block relationship points, so the client can switch
    entity context as it descends into a related container."""

    relationship_id: uuid.UUID
    related_entity_id: uuid.UUID
    kind: str  # "to_one" (section) | "to_many" (table/block)
    name: str


class FormRenderRead(BaseModel):
    """Everything a renderer needs: the authoring tree, a resolved field catalog
    for every entity it touches, relationship targets, and prefilled values."""

    form_id: uuid.UUID
    form_name: str
    description: str | None
    status: str  # link/record status: pending | submitted | expired | editable
    root_entity_id: uuid.UUID | None  # None for a standalone (no-entity) view
    config: FormConfig
    catalog: list[EntityCatalogEntry] = Field(default_factory=list)
    relationships: list[RelationshipMeta] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)  # prefilled root values
    # relationship_id -> {"values": {...}} (1:1) or {"rows": [{...}]} (1:M)
    related: dict[str, Any] = Field(default_factory=dict)


class FormSubmit(BaseModel):
    """A submission: root field values plus per-relationship related data.

    ``related`` is keyed by ``relationship_id``; a 1:1 carries ``{"values": {...}}``
    and a 1:M carries ``{"rows": [{...}, ...]}``. Only values for fields the tree
    actually exposes are honoured; everything else is dropped server-side."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)
    related: dict[str, Any] = Field(default_factory=dict)


# Back-compat alias for the public router/tests during the cutover.
PublicFormSubmit = FormSubmit
PublicFormRead = FormRenderRead
