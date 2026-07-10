"""Report model — a saved aggregation query plus its visualization.

A ``Report`` is an org-scoped, named GROUP BY / metric query over one custom
entity (``query`` — an :class:`~api.schemas.aggregate.AggregateQuery`) together
with a chart/table ``viz`` spec describing how to render the result. It is the
durable artifact behind the reporting engine: dashboards and the ``chart`` /
``metric`` view elements reference a report by id, and it travels in the org
import/export bundle. RLS-scoped like every tenant table.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org


class Report(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_report_slug_per_org"),)

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A report always aggregates over exactly one entity.
    entity_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="CASCADE"), index=True
    )
    # The AggregateQuery (group_by / metrics / filters / having / order / limit).
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # The Visualization spec (chart type, axes, series, options).
    viz: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    org: Mapped[Org] = relationship()
