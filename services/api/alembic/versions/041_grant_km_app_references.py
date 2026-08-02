"""Grant ``app_user`` REFERENCES on ``orgs`` so runtime entity DDL can add its FK.

Migration 035 gave ``km_app`` ``CREATE ON SCHEMA public`` so the entity authoring
paths could create their ``ce_*`` tables at runtime — but creating the table is only
half of it. ``SchemaManager.create_entity_table`` then runs::

    ALTER TABLE ce_<uuid> ADD CONSTRAINT fk_<uuid>
        FOREIGN KEY (org_id) REFERENCES orgs(id) ON DELETE CASCADE

and Postgres requires the **REFERENCES** privilege on the *referenced* table to
create a foreign key against it. Neither ``app_user`` nor ``km_app`` held it, so
every runtime entity creation failed with ``permission denied for table orgs`` —
surfacing as a 500 from ``POST /entity-definitions/`` and as
``import failed: … permission denied for table orgs`` from ``POST /migration/import``
for any bundle carrying entities. The existing ``ce_*`` tables predate the switch to
the non-superuser role: they were created by the ``redarch`` admin connection and
own their FKs, which is why the gap stayed hidden.

Granted to ``app_user`` rather than ``km_app`` so it follows the same inheritance
path as every other table privilege (migration 007 grants CRUD to ``app_user``;
``km_app`` is a member of it via 035). REFERENCES is strictly narrower than what
``app_user`` already holds on ``orgs`` — it has full SELECT/INSERT/UPDATE/DELETE —
so this widens no data access; it only permits pointing a foreign key at the table.

Guarded on the role existing so it is a harmless no-op anywhere ``app_user`` is not
used, and idempotent because GRANT is repeatable.

Revision ID: 041
Revises: 040
Create Date: 2026-08-01
"""

from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
                GRANT REFERENCES ON TABLE orgs TO app_user;
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
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
                REVOKE REFERENCES ON TABLE orgs FROM app_user;
            END IF;
        END
        $$;
        """
    )
