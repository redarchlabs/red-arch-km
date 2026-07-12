"""Add a GUC-gated permissive RLS bypass policy to every FORCE-RLS table.

Background — why this exists
----------------------------
KM2's tenant isolation uses ``FORCE ROW LEVEL SECURITY`` plus a *privileged
connection role* (historically the Postgres superuser, which bypasses RLS) for
its cross-org / no-tenant-context paths (``get_db``, the workflow/agent poll
sweeps, site-admin, provisioning). That model cannot run on Google Cloud SQL,
whose ``cloudsqlsuperuser`` cannot hold ``BYPASSRLS``.

This migration adopts the Cobalt pattern: the app connects as a plain
non-``BYPASSRLS`` role (``km_app``) and cross-tenant access is granted by a
**permissive policy gated on a session GUC** instead of by the connection role.
RLS policies are OR-combined (permissive), so a row is visible/writable when
*either* the per-tenant policy matches ``app.current_tenant_id`` *or* the caller
has opted into ``app.bypass = 'on'``. Normal request paths never set the GUC, so
RLS still fully isolates them; only the enumerated privileged paths set it (see
``api/db_scope.py``).

The bypass is deliberately read-AND-write (``USING`` + ``WITH CHECK``, all
commands) because the workflow/agent engine claims work across every org in a
single ``UPDATE … FOR UPDATE SKIP LOCKED`` statement, which a single-tenant GUC
cannot express. This is an exact translation of the previous superuser-vs-
app_user split — same security posture, GUC-enforced instead of role-enforced.

Catalog-driven so it covers every table that currently has FORCE RLS regardless
of which migration added it. CONVENTION: any future migration that adds a
tenant-scoped (FORCE-RLS) table MUST also add an ``admin_bypass_all`` policy to
it (mirror the CREATE POLICY below). Dynamically-created ``ce_*`` entity tables
get theirs from ``SchemaManager._apply_rls``.

Revision ID: 034
Revises: 033
Create Date: 2026-07-11
"""

from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


_APPLY = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND c.relforcerowsecurity
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS admin_bypass_all ON public.%I', r.relname);
        EXECUTE format(
            $f$CREATE POLICY admin_bypass_all ON public.%I
                   USING (current_setting('app.bypass', true) = 'on')
                   WITH CHECK (current_setting('app.bypass', true) = 'on')$f$,
            r.relname
        );
    END LOOP;
END
$$;
"""

_DROP = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND c.relforcerowsecurity
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS admin_bypass_all ON public.%I', r.relname);
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    op.execute(_APPLY)


def downgrade() -> None:
    op.execute(_DROP)
