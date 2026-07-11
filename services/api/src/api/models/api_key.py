"""API key model — an org-scoped programmatic credential for the enterprise API.

An :class:`ApiKey` authenticates external callers to the public ``/api/v1`` REST
surface. It is org-scoped (RLS, like every tenant table) and stores
only the **SHA-256 hash** of the key — never the plaintext, which is shown to the
creating admin exactly once. The auth path resolves a presented key by looking up
``key_hash`` (globally unique + indexed) on a privileged session, then downgrades
to the key's org, mirroring :class:`WorkflowInboundEndpoint`.

Access is gated two ways: ``scopes`` restricts which *operations* the key may
perform, and ``revoked_at`` / ``expires_at`` bound its *lifetime*.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org


class ApiKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("key_hash", name="uq_api_key_hash"),)

    name: Mapped[str] = mapped_column(String(120))
    # Non-secret public identifier for the admin list (e.g. "km2_AbC12").
    key_prefix: Mapped[str] = mapped_column(String(20))
    # SHA-256 hex of the full plaintext key; the only stored form of the secret.
    # The unique constraint below already provides the lookup index — no separate
    # index=True (which would create a redundant second b-tree on the same column).
    key_hash: Mapped[str] = mapped_column(String(64))
    # Permission scope strings, e.g. ["reports:run", "entities:read"].
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # Audit: which admin minted the key (nulled if that user is later removed).
    created_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    org: Mapped[Org] = relationship()
