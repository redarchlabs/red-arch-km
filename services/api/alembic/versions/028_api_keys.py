"""API keys — org-scoped programmatic credentials for the enterprise API.

Each row is a hashed API key (never the plaintext) that authenticates external
callers to the public ``/api/v1`` REST + GraphQL surface. Keys are org-scoped
(RLS, same hardened policy template as the rest of the schema), carry a set of
permission ``scopes``, and may be given an expiry. Only the SHA-256 hash of the
key is stored; the plaintext is shown to the creating admin exactly once. Lookups
on the auth path are by ``key_hash`` (indexed, globally unique) on a privileged
session, mirroring ``workflow_inbound_endpoints.token_hash``.

Revision ID: 028
Revises: 027
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None

_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for suffix, action, clause in _POLICIES:
        op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({_HARDENED})")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        # Non-secret public identifier shown in the admin list (e.g. "km2_AbC12").
        sa.Column("key_prefix", sa.String(20), nullable=False),
        # SHA-256 hex of the full plaintext key; the only stored form of the secret.
        sa.Column("key_hash", sa.String(64), nullable=False),
        # List of permission scope strings (e.g. ["reports:run", "entities:read"]).
        sa.Column("scopes", postgresql.JSONB, nullable=False, server_default="[]"),
        # Audit: which admin minted the key. SET NULL so removing a user keeps the key.
        sa.Column(
            "created_by_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orgs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        # Global uniqueness on the hash: the auth path resolves a presented key to
        # exactly one row across all tenants, so collisions must be impossible. The
        # unique constraint's index also serves the by-hash auth lookup, so no
        # separate index is created (it would be redundant).
        sa.UniqueConstraint("key_hash", name="uq_api_key_hash"),
    )
    _apply_rls("api_keys")


def downgrade() -> None:
    for suffix, _a, _c in _POLICIES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON api_keys")
    op.drop_table("api_keys")
