"""Ensure the non-superuser app_user role exists with the grants RLS needs.

The API's tenant sessions (get_tenant_db) drop to `app_user` via
`SET LOCAL ROLE app_user` so PostgreSQL row-level security is actually enforced
(superuser/BYPASSRLS connections bypass RLS even under FORCE ROW LEVEL SECURITY).

The role is created by docker/init-db.sql, but that script only runs on a *fresh*
Postgres data volume. Any environment whose database predates the role — or was
initialized without that script (e.g. the host Postgres used in dev) — would have
no `app_user`, and `SET LOCAL ROLE app_user` would raise at request time. This
migration makes the role + grants exist idempotently in every environment before
the app relies on them.

Grants use ALL TABLES IN SCHEMA public (a superset of migration 001's per-table
grants) so tables added since 001 are covered too. No sequence grants are needed:
all primary keys are application-generated uuid4 and timestamps use
`server_default now()`.

Revision ID: 007
Revises: 006
Create Date: 2026-07-05
"""

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CREATE ROLE has no IF NOT EXISTS, so guard with a DO block. LOGIN is
    # intentionally omitted here — runtime access is via SET ROLE from the
    # privileged connection role, not a direct app_user login (docker/init-db.sql
    # grants LOGIN where a direct login is wanted).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user")


def downgrade() -> None:
    # Revoke the grants but keep the role: other environments (docker compose)
    # provision app_user independently, and DROP ROLE fails if it owns objects
    # or is otherwise referenced. Revoking is the safely reversible half.
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM app_user")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_user")
