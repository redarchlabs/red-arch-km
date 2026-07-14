"""Release / change-set promotion models.

See migration ``038_release_management`` for the schema and rationale. A
:class:`Release` is an immutable frozen snapshot of selected config plus a
governance status; a :class:`ReleasePromotion` is one execution of a release
against a :class:`PromotionTarget`, carrying a reverse snapshot for rollback.

Status vocabularies are plain strings + CHECK (mirroring ``workflow_versions``);
the ``StrEnum``s below are for type-safe use in the service layer and are stored
as their string values.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, UUIDMixin


class PromotionTargetKind(StrEnum):
    LOCAL_ORG = "local_org"
    REMOTE_INSTANCE = "remote_instance"


class ReleaseStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class PromotionStatus(StrEnum):
    PENDING = "pending"
    PROMOTING = "promoting"
    PROMOTED = "promoted"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PromotionTarget(Base, UUIDMixin, TimestampMixin):
    """Where a release can be promoted: another org in this DB, or a remote KM2.

    Owned (and RLS-scoped) by the SOURCE org. For ``remote_instance`` targets the
    API key is Fernet-encrypted at rest (``secret_encrypted``) exactly like a
    workflow connection and is never returned by the API.
    """

    __tablename__ = "promotion_targets"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # local_org
    target_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True
    )
    # remote_instance
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    remote_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Release(Base, UUIDMixin, TimestampMixin):
    """A named, frozen snapshot of selected config + a governance status.

    ``org_id`` is the SOURCE org (deliberately not a separate ``source_org_id`` so
    the standard tenant policy applies unchanged). ``bundle`` holds the frozen
    export inline (config-only common case) or ``bundle_uri`` references it in
    object storage; the frozen shape is guarded by a CHECK.
    """

    __tablename__ = "releases"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ReleaseStatus.DRAFT.value)
    bundle: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    bundle_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    bundle_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_format_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReleaseItem(Base, UUIDMixin, TimestampMixin):
    """One object inside a frozen release (a normalized inventory row).

    Lets the diff/promotion layers reason per object (by ``lineage_id`` +
    ``fingerprint``) without re-parsing the whole bundle JSONB.
    """

    __tablename__ = "release_items"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), index=True
    )
    object_type: Mapped[str] = mapped_column(String(40))
    lineage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    natural_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))


class ReleaseApproval(Base, UUIDMixin, TimestampMixin):
    """An append-only per-approver decision on a release (governance audit)."""

    __tablename__ = "release_approvals"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), index=True
    )
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    decision: Mapped[str] = mapped_column(String(12))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReleasePromotion(Base, UUIDMixin, TimestampMixin):
    """One execution of a release against one target, with rollback capture.

    ``org_id`` is the SOURCE org. Target facts are denormalized so the audit log
    survives target deletion. ``pre_state_bundle`` is the reverse snapshot re-applied
    on rollback.
    """

    __tablename__ = "release_promotions"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE")
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("promotion_targets.id", ondelete="SET NULL"), nullable=True
    )
    target_kind: Mapped[str] = mapped_column(String(20))
    target_label: Mapped[str] = mapped_column(String(200))
    target_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=PromotionStatus.PENDING.value)
    strategy: Mapped[str] = mapped_column(String(16), default="skip")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pre_state_bundle: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pre_state_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    pre_state_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollback_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("release_promotions.id", ondelete="SET NULL"), nullable=True
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
