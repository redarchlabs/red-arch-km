"""User and membership models."""

from __future__ import annotations

import uuid

# Association tables for M2M relationships on UserOrgMembership
from sqlalchemy import Boolean, Column, ForeignKey, String, Table, Text, UniqueConstraint
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Department, Group, Org, Region, Role

membership_regions = Table(
    "membership_regions",
    Base.metadata,
    Column("membership_id", UUID(as_uuid=True), ForeignKey("user_org_memberships.id", ondelete="CASCADE")),
    Column("region_id", UUID(as_uuid=True), ForeignKey("regions.id", ondelete="CASCADE")),
)

membership_departments = Table(
    "membership_departments",
    Base.metadata,
    Column("membership_id", UUID(as_uuid=True), ForeignKey("user_org_memberships.id", ondelete="CASCADE")),
    Column("department_id", UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE")),
)

membership_roles = Table(
    "membership_roles",
    Base.metadata,
    Column("membership_id", UUID(as_uuid=True), ForeignKey("user_org_memberships.id", ondelete="CASCADE")),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE")),
)

membership_groups = Table(
    "membership_groups",
    Base.metadata,
    Column("membership_id", UUID(as_uuid=True), ForeignKey("user_org_memberships.id", ondelete="CASCADE")),
    Column("group_id", UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE")),
)


class UserProfile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_profiles"

    # IdP subject ID (provider-neutral; unique identifier from the OIDC provider —
    # Keycloak historically, Clerk after Slice 6). Renamed from keycloak_sub (D3).
    auth_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_site_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Deactivated accounts are rejected at auth time (see auth/dependencies.py);
    # deactivation beats deleting because memberships/documents keep their author.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())

    memberships: Mapped[list[UserOrgMembership]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class UserOrgMembership(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_org_memberships"
    __table_args__ = (UniqueConstraint("profile_id", "org_id", name="uq_profile_org"),)

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))
    is_org_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    profile: Mapped[UserProfile] = relationship(back_populates="memberships")
    org: Mapped[Org] = relationship()

    regions: Mapped[list[Region]] = relationship(secondary=membership_regions)
    departments: Mapped[list[Department]] = relationship(secondary=membership_departments)
    roles: Mapped[list[Role]] = relationship(secondary=membership_roles)
    groups: Mapped[list[Group]] = relationship(secondary=membership_groups)
