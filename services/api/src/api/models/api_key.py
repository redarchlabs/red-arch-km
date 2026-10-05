"""API key model — an org-scoped programmatic credential for the enterprise API.

An :class:`ApiKey` authenticates external callers to the public ``/api/v1`` REST
surface. It is org-scoped (RLS, like every tenant table) and stores
only the **SHA-256 hash** of the key — never the plaintext, which is shown to the
creating admin exactly once. The auth path resolves a presented key by looking up
``key_hash`` (globally unique + indexed) on a privileged session, then downgrades
to the key's org, mirroring :class:`WorkflowInboundEndpoint`.

Access is gated three ways: ``scopes`` restricts which *operations* the key may
perform, ``revoked_at`` / ``expires_at`` bound its *lifetime*, and ``access_mode``
sets its *data visibility*:

* ``org`` (the default) — org-wide, today's behaviour for every existing key;
* ``scoped`` — the key reads with the masks of its own dimension assignments
  (``api_key_regions`` / ``_roles`` / ``_groups`` / ``_departments``), exactly as a
  member holding those assignments would, optionally narrowed further to a list of
  folders (``api_key_folders``, with their subfolders). Folders only ever narrow.

See ``api.services.api_key_scope``. The join tables have no ``org_id`` (and so no
RLS): every id written to them is validated against the key's org at mint time.
Deleting a key cascades to its rows; deleting a referenced role/folder/... is
refused while a key holds it (``ON DELETE NO ACTION``, deferred to commit, so
deleting the whole org — which cascades to both sides — still succeeds).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org

ACCESS_MODE_ORG = "org"
ACCESS_MODE_SCOPED = "scoped"


def _assignment_table(name: str, column: str, target: str) -> Table:
    """``(api_key_id, <column>)`` — cascade with the key, NO ACTION on the item."""
    return Table(
        name,
        Base.metadata,
        Column("api_key_id", UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True),
        # NO ACTION, DEFERRABLE INITIALLY DEFERRED: checked at commit. An org delete
        # cascades to its roles/folders before (or without) its keys — PostgreSQL
        # runs each cascade as its own query and checks a non-deferred FK at the end
        # of that query — so anything stricter breaks deleting the org.
        Column(
            column,
            UUID(as_uuid=True),
            ForeignKey(f"{target}.id", deferrable=True, initially="DEFERRED"),
            primary_key=True,
            index=True,
        ),
    )


api_key_regions = _assignment_table("api_key_regions", "region_id", "regions")
api_key_roles = _assignment_table("api_key_roles", "role_id", "roles")
api_key_groups = _assignment_table("api_key_groups", "group_id", "groups")
api_key_departments = _assignment_table("api_key_departments", "department_id", "departments")
api_key_folders = _assignment_table("api_key_folders", "folder_id", "folders")


class ApiKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_key_hash"),
        CheckConstraint("access_mode IN ('org', 'scoped')", name="ck_api_keys_access_mode"),
    )

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
    # "org" = org-wide; "scoped" = its own dimension/folder assignments (see the
    # module docstring). A scoped key with no assignment rows is refused, never
    # widened (api.services.api_key_scope).
    access_mode: Mapped[str] = mapped_column(String(10), default=ACCESS_MODE_ORG, server_default=ACCESS_MODE_ORG)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    org: Mapped[Org] = relationship()
