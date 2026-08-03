"""View model.

A ``View`` is an org-scoped composable *screen*: a layout tree (the same element
system as forms, plus ``form_ref`` widgets and workflow-run buttons) rendered by
the shared ``FormRenderer``. A view may optionally bind to an entity (so its
fields/calculated/buttons can reference a record) or stand alone as a dashboard
of embedded forms + actions. RLS-scoped like every tenant table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, LineageMixin, TimestampMixin, UUIDMixin
from api.models.org import Org


class View(Base, UUIDMixin, TimestampMixin, LineageMixin):
    __tablename__ = "views"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_view_slug_per_org"),)

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional entity binding: when set, the view renders/edits a record of it.
    entity_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- anonymous access (off unless explicitly enabled; see migration 042) ---
    # SHA-256 of the share token. The raw token is shown once at enable time and
    # never persisted, so a database read cannot recover a working link.
    public_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The record an anonymous render is PINNED to. Without this a token would be a
    # licence to walk every record of the view's entity.
    public_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # …unless the page is about "whatever is happening now" rather than one fixed
    # thing. Then the newest record is resolved per request instead (migration 044),
    # the same rule the authenticated ``record_id=latest`` sentinel uses. Still
    # server-side and still scoped to this view's own entity, so the caller cannot
    # choose a row — only the definition of "which one" changes.
    public_record_follow: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    public_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    public_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    org: Mapped[Org] = relationship()
