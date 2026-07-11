"""MCP server connection — an org-scoped, encrypted handle to an external MCP
server the agent runtime can consume, modeled on ``WorkflowConnection``.

Secrets (bearer token / api key) are Fernet-encrypted at rest and decrypted only
at call time; ``config`` holds non-secret transport details (headers, args, env
key names). ``transport`` selects how the MCP client connects.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.models.base import Base, TimestampMixin, UUIDMixin

MCP_TRANSPORTS = ("stdio", "http", "sse")
# Static secret vs OAuth 2.1 browser flow.
MCP_AUTH_TYPES = ("none", "bearer", "api_key", "oauth")
# OAuth token identity: one shared org install, or a token per user.
MCP_OAUTH_IDENTITIES = ("org", "user")


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

    # --- OAuth 2.1 (auth_type == "oauth") -----------------------------------
    # "org" = one shared install (tokens on this row); "user" = one token per user
    # (in mcp_server_user_tokens). config.oauth holds the non-secret app details
    # (authorization_endpoint, token_endpoint, registration_endpoint, client_id, scopes).
    oauth_identity: Mapped[str] = mapped_column(String(10), default="org")
    oauth_client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Org-mode tokens (Fernet). User-mode tokens live in mcp_server_user_tokens.
    oauth_access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )


class McpServerUserToken(Base, UUIDMixin, TimestampMixin):
    """A per-user OAuth token for an ``oauth_identity == "user"`` MCP server."""

    __tablename__ = "mcp_server_user_tokens"
    __table_args__ = (
        UniqueConstraint("mcp_server_id", "user_profile_id", name="uq_mcp_user_token"),
    )

    mcp_server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    user_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), index=True
    )
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )


class McpOAuthFlow(Base, UUIDMixin, TimestampMixin):
    """An in-flight authorization (between /oauth/start and the callback).

    Holds the PKCE ``code_verifier`` + a random ``state`` so the public callback can
    resolve which (server, user) it belongs to without a session. Deleted on
    completion; a sweep can drop stale rows."""

    __tablename__ = "mcp_oauth_flows"
    __table_args__ = (UniqueConstraint("state", name="uq_mcp_oauth_flow_state"),)

    mcp_server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    # Null for an org-mode install; set for a user-mode connect.
    user_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(128), index=True)
    code_verifier: Mapped[str] = mapped_column(String(256))
    redirect_uri: Mapped[str] = mapped_column(String(1000))

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
