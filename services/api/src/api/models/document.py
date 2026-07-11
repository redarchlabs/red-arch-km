"""Document, folder, and tag models."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org

document_tags = Table(
    "document_tags",
    Base.metadata,
    Column("document_id", UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE")),
)


class ProcessingStatus(StrEnum):
    """Canonical document ingestion states.

    These are the exact string values the worker writes back via the status
    callback (`api/routers/internal.py`) and that the UI renders. Keep this in
    lockstep with the worker constants and the UI's status types — a mismatch
    (the historical `COMPLETE`/`ERROR` values here vs the worker's
    `SUCCESS`/`FAILED`) silently breaks status badges end to end.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @classmethod
    def terminal(cls) -> frozenset[ProcessingStatus]:
        """States a document rests in once ingestion has concluded.

        These are **sticky** against the worker's status callback: a duplicate
        or redelivered task execution (Celery ``acks_late`` can run concurrent
        copies) must not revert CANCELLED/SUCCESS/FAILED back to PROCESSING. Only
        an explicit reprocess re-opens the document, via a separate PENDING write.
        """
        return frozenset({cls.SUCCESS, cls.FAILED, cls.CANCELLED})


class Folder(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "folders"
    __table_args__ = (UniqueConstraint("org_id", "name", "parent_id", name="uq_folder_name_per_org_parent"),)

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    dot_path: Mapped[str] = mapped_column(Text, default="", index=True)

    # Permission masks (PostgreSQL ARRAY of BIGINT)
    view_permission_masks: Mapped[list[int]] = mapped_column(ARRAY(BIGINT), default=list, server_default="{}")
    contributor_permission_masks: Mapped[list[int]] = mapped_column(ARRAY(BIGINT), default=list, server_default="{}")
    viewer_permissions_config: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    contributor_permissions_config: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Foreign keys
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("folders.id", ondelete="CASCADE"), nullable=True
    )

    org: Mapped[Org] = relationship()
    parent: Mapped[Folder | None] = relationship(remote_side="Folder.id", back_populates="children")
    children: Mapped[list[Folder]] = relationship(back_populates="parent")


class Tag(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_tag_per_org"),)

    name: Mapped[str] = mapped_column(String(100))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))

    org: Mapped[Org] = relationship()


class Document(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("org_id", "document_key", name="uq_doc_key_per_org"),)

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_key: Mapped[str] = mapped_column(String(255), default=lambda: str(uuid.uuid4()))
    document_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Size of the original in bytes (uploaded file size, or byte-length of
    # pasted text). Nullable for rows created before this was tracked.
    size_bytes: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    processing_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    processing_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=dict)
    # Celery task id of the in-flight (or most recent) ingest job. Persisted at
    # dispatch so the ingest can be cancelled (revoked) and so its per-job logs
    # can be correlated. NULL for rows that never dispatched (broker outage) or
    # predate this column.
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True, default=dict)
    use_knowledge_graph: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Per-document permissions, independent of the folder. Seeded from the
    # folder at creation (the default) and overridable via the document's
    # Properties. A NULL config means "no override" — entitlement then falls
    # back to the folder's masks. Mirrors the Folder columns above.
    view_permission_masks: Mapped[list[int]] = mapped_column(ARRAY(BIGINT), default=list, server_default="{}")
    contributor_permission_masks: Mapped[list[int]] = mapped_column(ARRAY(BIGINT), default=list, server_default="{}")
    viewer_permissions_config: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    contributor_permissions_config: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    # Foreign keys
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("folders.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )

    org: Mapped[Org] = relationship()
    folder: Mapped[Folder | None] = relationship()
    tags: Mapped[list[Tag]] = relationship(secondary=document_tags)


class DocumentAccess(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "document_access"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"))
    folder_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("folders.id", ondelete="CASCADE"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))


class DocumentAttributeDefinition(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "document_attribute_definitions"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_attr_slug_per_org"),)

    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100))
    attribute_type: Mapped[str] = mapped_column(String(20), default="freeform")
    picklist_options: Mapped[list[Any] | None] = mapped_column(JSONB, default=list)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped[int] = mapped_column(Integer, default=0)

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))
    org: Mapped[Org] = relationship()
