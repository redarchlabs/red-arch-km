"""Organization and permission hierarchy models."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin


class Org(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "orgs"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_knowledge_graph: Mapped[bool] = mapped_column(Boolean, default=True)
    openai_api_key: Mapped[str | None] = mapped_column(String(800), nullable=True)
    permission_number: Mapped[int] = mapped_column(SmallInteger, default=0)

    # Relationships
    regions: Mapped[list[Region]] = relationship(back_populates="org", cascade="all, delete-orphan")
    departments: Mapped[list[Department]] = relationship(back_populates="org", cascade="all, delete-orphan")
    roles: Mapped[list[Role]] = relationship(back_populates="org", cascade="all, delete-orphan")
    groups: Mapped[list[Group]] = relationship(back_populates="org", cascade="all, delete-orphan")


class Region(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "regions"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_region_name_per_org"),)

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_number: Mapped[int] = mapped_column(SmallInteger, default=0)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))

    org: Mapped[Org] = relationship(back_populates="regions")


class Department(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_dept_name_per_org"),)

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_number: Mapped[int] = mapped_column(SmallInteger, default=0)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))

    org: Mapped[Org] = relationship(back_populates="departments")


class Role(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_role_name_per_org"),)

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_number: Mapped[int] = mapped_column(SmallInteger, default=0)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))

    org: Mapped[Org] = relationship(back_populates="roles")


class Group(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_group_name_per_org"),)

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission_number: Mapped[int] = mapped_column(SmallInteger, default=0)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"))

    org: Mapped[Org] = relationship(back_populates="groups")
