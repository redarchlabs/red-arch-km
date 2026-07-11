"""OAuth 2.1 for MCP servers: per-org or per-user browser-flow authentication.

Adds OAuth columns to ``mcp_servers`` (identity mode, client secret, org-mode
tokens), a per-user token table, and an in-flight authorization table. Secrets +
tokens are Fernet-encrypted; the token/flow tables are org-scoped (RLS).

Revision ID: 032
Revises: 031
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None

_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]
_TABLES = ["mcp_server_user_tokens", "mcp_oauth_flows"]


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def _org_col():
    return sa.Column(
        "org_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True,
    )


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("oauth_identity", sa.String(10), nullable=False, server_default="org"))
    op.add_column("mcp_servers", sa.Column("oauth_client_secret_encrypted", sa.Text, nullable=True))
    op.add_column("mcp_servers", sa.Column("oauth_access_token_encrypted", sa.Text, nullable=True))
    op.add_column("mcp_servers", sa.Column("oauth_refresh_token_encrypted", sa.Text, nullable=True))
    op.add_column("mcp_servers", sa.Column("oauth_token_expires_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "mcp_server_user_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mcp_server_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "user_profile_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("access_token_encrypted", sa.Text, nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text, nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        _org_col(),
        *_timestamps(),
        sa.UniqueConstraint("mcp_server_id", "user_profile_id", name="uq_mcp_user_token"),
    )

    op.create_table(
        "mcp_oauth_flows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mcp_server_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "user_profile_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("state", sa.String(128), nullable=False, index=True),
        sa.Column("code_verifier", sa.String(256), nullable=False),
        sa.Column("redirect_uri", sa.String(1000), nullable=False),
        _org_col(),
        *_timestamps(),
        sa.UniqueConstraint("state", name="uq_mcp_oauth_flow_state"),
    )

    for table in _TABLES:
        _apply_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        for suffix, _a, _c in _POLICIES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON {table}")
        op.drop_table(table)
    for col in (
        "oauth_token_expires_at", "oauth_refresh_token_encrypted", "oauth_access_token_encrypted",
        "oauth_client_secret_encrypted", "oauth_identity",
    ):
        op.drop_column("mcp_servers", col)
