"""SQLAlchemy base model with common mixins."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class UUIDMixin:
    """UUID primary key mixin."""

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Created/updated timestamp mixin."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantMixin:
    """Tenant ID for RLS-protected tables."""

    # This column is used by PostgreSQL RLS policies.
    # The API sets `SET app.current_tenant_id` per request so queries
    # are automatically scoped to the current tenant.
    pass  # org_id FK is defined per model since relationship config varies


class LineageMixin:
    """Durable cross-environment identity for promotable config objects.

    ``NULL`` means **self-origin**: the object was authored in this environment
    and its lineage identity is simply its own ``id`` — so every existing row is
    already correctly identified with no backfill. It is *stamped* (set non-NULL)
    only on objects copied in from another environment, so a later promotion
    re-targets the same row even after the object's slug/name is renamed.

    See migration ``037_config_lineage`` and ``api.services.migration`` (exporter
    emits ``lineage_id := row.lineage_id or row.id``; importer matches on
    ``(org_id, lineage_id)`` first).
    """

    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
