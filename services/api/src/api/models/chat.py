"""Chat session model."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org


class ChatSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "chat_sessions"

    chat_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=dict)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Foreign keys
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    # A chat session is always owned by its creating user; there is no valid
    # user-less session (list_for_user filters by user_id, so a NULL owner is
    # unreachable). CASCADE removes a user's private conversations when their
    # profile is deleted rather than orphaning them.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )

    org: Mapped[Org] = relationship()
