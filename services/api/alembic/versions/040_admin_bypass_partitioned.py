"""Add the ``admin_bypass_all`` policy to partitioned FORCE-RLS tables.

Migration 034 added the GUC-gated bypass policy to "every table that currently
has FORCE RLS", but its catalog query filtered on ``c.relkind = 'r'`` — plain
tables only. Partitioned parents have ``relkind = 'p'``, so the four partitioned
workflow-engine tables (``workflow_runs``, ``workflow_run_steps``,
``workflow_run_tokens``, ``workflow_outbox``) were silently skipped. RLS on a
partitioned table is evaluated against the parent's policies for any statement
routed through the parent, which is how the app always accesses them — so on
those tables every ``app.bypass = 'on'`` path was broken once the app connected
as the non-BYPASSRLS ``km_app`` role:

* manual workflow runs (``POST /api/workflows/{id}/run`` inserts a
  ``workflow_runs`` row from the bypass-scoped ``get_db`` session) failed with
  "new row violates row-level security policy";
* the cross-org sweep claims (``UPDATE … SKIP LOCKED`` on ``workflow_outbox`` /
  ``workflow_run_tokens``) matched zero rows, so the outbox never drained.

This re-runs 034's loop with ``relkind IN ('r', 'p')`` restricted to the
partitioned parents. DROP + CREATE keeps it idempotent for environments where
the policy was already applied by hand. Leaf partitions need nothing: they are
only reached through the parent, and the parent's policies govern that access.

Detection query (must return zero rows on a healthy database):

    SELECT c.relname FROM pg_class c
    WHERE c.relforcerowsecurity
      AND NOT EXISTS (SELECT 1 FROM pg_policy p
                      WHERE p.polrelid = c.oid
                        AND p.polname = 'admin_bypass_all');

Revision ID: 040
Revises: 039
Create Date: 2026-07-17
"""

from alembic import op

revision = "040"
down_revision = "039"
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
          AND c.relkind = 'p'
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
          AND c.relkind = 'p'
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
