"""Grant the runtime ``km_app`` login role its memberships (Cloud SQL bootstrap).

The app connects as the non-superuser ``km_app`` role (see migration 034 /
docker/init-db.sql). It needs:
  * membership in ``app_user`` — inherits USAGE on schema + per-table CRUD grants
  * ``CREATE`` on schema ``public`` — the entity authoring paths create ``ce_*``
    tables at runtime

In local dev these grants are applied by ``docker/init-db.sql`` when the Postgres
volume is first initialized. On Google Cloud SQL there is no init-db hook: the
``km_app`` user is created out-of-band by Terraform (``google_sql_user``), and
these grants run here — as the migration/admin role, after ``app_user`` exists
(migration 007). Guarded on ``km_app`` existing so it is a harmless no-op on any
environment that doesn't use that role (and idempotent — GRANT is repeatable).

Role membership in a migration mirrors migration 007 (which creates ``app_user``
and grants it schema/table privileges); this is the same operational lane.

Revision ID: 035
Revises: 034
Create Date: 2026-07-12
"""

from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'km_app') THEN
                GRANT app_user TO km_app;
                GRANT CREATE ON SCHEMA public TO km_app;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'km_app') THEN
                REVOKE CREATE ON SCHEMA public FROM km_app;
                REVOKE app_user FROM km_app;
            END IF;
        END
        $$;
        """
    )
