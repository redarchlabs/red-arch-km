"""Intake-form models.

A ``Form`` is a reusable, org-scoped definition binding a set of an entity's
fields (and, later, related child records) into a public intake form. A
``FormLink`` is a single-use, token-addressed invitation that binds one form to
one *target record* to update; the token is emailed to an external user who
fills the form on a public, unauthenticated page.

Both tables are RLS-scoped like every tenant table. The public path resolves a
link by ``token_hash`` on the privileged (BYPASSRLS) connection — it has no org
context yet — then drops to ``app_user`` with the link's org set as the tenant
GUC before touching any entity data.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org

# A link is minted pending, becomes submitted on completion, or expired/revoked.
FORM_LINK_STATUSES = ("pending", "submitted", "expired", "revoked")


class Form(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "forms"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_form_slug_per_org"),)

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="CASCADE"), index=True
    )
    # {fields: [{slug,label?,required?,help?}], sections: [{relationship_id,mode,label?,fields:[...]}]}
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    org: Mapped[Org] = relationship()


class FormLink(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "form_links"

    form_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forms.id", ondelete="CASCADE"), index=True
    )
    # The existing record this submission updates (forms update the emailed record).
    target_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # sha256 of the raw token; the raw token is shown once at creation and never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
