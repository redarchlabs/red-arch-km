"""MCP server connection — an org-scoped, encrypted handle to an external MCP
server the agent runtime can consume, modeled on ``WorkflowConnection``.

Secrets (bearer token / api key) are Fernet-encrypted at rest and decrypted only
at call time; ``config`` holds non-secret transport details (headers, args, env
key names). ``transport`` selects how the MCP client connects.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, UUIDMixin

MCP_TRANSPORTS = ("stdio", "http", "sse")


class McpServer(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_mcp_server_name_per_org"),)

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport: Mapped[str] = mapped_column(String(10), default="http")
    # For stdio: the command line to spawn. For http/sse: unused.
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For http/sse: the server URL. For stdio: unused.
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Non-secret transport config: {"headers": {...}, "args": [...], "auth_type": "bearer"}.
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Fernet ciphertext of the bearer/api-key secret; NULL when the server needs none.
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
