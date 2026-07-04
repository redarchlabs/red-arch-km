"""Rename user_profiles.keycloak_sub to auth_subject (provider-neutral, D3/AC-4.1).

After the Keycloak -> Clerk migration (Slice 6) the IdP-subject column name
``keycloak_sub`` is misleading — it now stores Clerk subjects. This renames it to
the provider-neutral ``auth_subject``, reaching parity with the Go stack
(``services/api-go`` migration ``002_rename_keycloak_sub_to_auth_subject``).

Pure rename: the UNIQUE + NOT NULL constraints and all foreign keys (which
reference ``user_profiles.id``, not this column) are preserved, so memberships
and the access_mask are unaffected. Existing rows keep their stored subject until
a verified-email relink rebinds it to the Clerk subject on first Clerk login.

Revision ID: 003
Revises: 002
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "user_profiles",
        "keycloak_sub",
        new_column_name="auth_subject",
        existing_type=sa.String(255),
        existing_nullable=False,
    )
    op.execute("ALTER INDEX ix_user_profiles_keycloak_sub RENAME TO ix_user_profiles_auth_subject")


def downgrade() -> None:
    op.execute("ALTER INDEX ix_user_profiles_auth_subject RENAME TO ix_user_profiles_keycloak_sub")
    op.alter_column(
        "user_profiles",
        "auth_subject",
        new_column_name="keycloak_sub",
        existing_type=sa.String(255),
        existing_nullable=False,
    )
