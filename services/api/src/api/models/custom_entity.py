"""Custom-entity catalog models.

These three tables describe user-defined entity types ("Customer", "Patient")
and drive the runtime DDL that creates one *real* physical Postgres table per
entity type. The physical tables themselves are created by ``SchemaManager``
and are not ORM-mapped; only this catalog is.

All three are tenant-scoped (``org_id`` FK + RLS, applied in migration 008),
mirroring the existing ``DocumentAttributeDefinition`` pattern.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org


class EntityDefinition(Base, UUIDMixin, TimestampMixin):
    """A user-defined entity type, backed by a physical table ``ce_<hex>``."""

    __tablename__ = "entity_definitions"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_entity_def_slug_per_org"),
        # Physical table names are global in Postgres, so uniqueness is global.
        UniqueConstraint("physical_table", name="uq_entity_def_physical_table"),
    )

    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(63))
    # System-generated physical table name (``ce_<hex32>``). Stored so it is
    # never re-derived from mutable input; a slug rename is therefore zero-DDL.
    physical_table: Mapped[str] = mapped_column(String(63))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )

    org: Mapped[Org] = relationship()
    fields: Mapped[list[EntityField]] = relationship(
        back_populates="definition",
        cascade="all, delete-orphan",
        order_by="EntityField.order",
    )


class EntityField(Base, UUIDMixin, TimestampMixin):
    """A scalar field on an entity, backed by a physical column ``f_<hex>``."""

    __tablename__ = "entity_fields"
    __table_args__ = (UniqueConstraint("entity_definition_id", "slug", name="uq_entity_field_slug_per_def"),)

    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(63))
    # System-generated physical column name (``f_<hex32>``).
    physical_column: Mapped[str] = mapped_column(String(63))
    field_type: Mapped[str] = mapped_column(String(30))
    picklist_options: Mapped[list[Any] | None] = mapped_column(JSONB, default=list)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    is_unique: Mapped[bool] = mapped_column(Boolean, default=False)
    default_value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    entity_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="CASCADE"), index=True
    )

    definition: Mapped[EntityDefinition] = relationship(back_populates="fields")


class EntityRelationship(Base, UUIDMixin, TimestampMixin):
    """A relationship between two entities.

    ``physical_name`` is the FK column (``r_<hex>``) for to-one cardinalities,
    or the join-table name (``cej_<hex>``) for many-to-many.
    """

    __tablename__ = "entity_relationships"
    __table_args__ = (UniqueConstraint("source_definition_id", "slug", name="uq_entity_rel_slug_per_source"),)

    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(63))
    cardinality: Mapped[str] = mapped_column(String(20))
    on_delete: Mapped[str] = mapped_column(String(10), default="SET NULL")
    physical_name: Mapped[str] = mapped_column(String(63))
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    source_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="CASCADE"), index=True
    )
    target_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_definitions.id", ondelete="RESTRICT"), index=True
    )

    source: Mapped[EntityDefinition] = relationship(foreign_keys=[source_definition_id])
    target: Mapped[EntityDefinition] = relationship(foreign_keys=[target_definition_id])
