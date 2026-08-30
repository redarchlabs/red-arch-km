"""Make workflow partition maintenance actually work, and add retention.

``workflow_ensure_partitions()`` has never successfully created a partition on
any stack. Every workflow row ever written lives in the DEFAULT partitions, so
the partitioning added in 009/018 has been buying nothing: no pruning, and no
way to drop old data except a DELETE over the whole table.

Three faults, stacked — each hidden behind the one before it:

1. **The function ran as INVOKER.** ``CREATE TABLE ... PARTITION OF`` requires
   ownership of the parent. The app connects as ``km_app``, which owns none of
   these tables, so the very first statement raised *"must be owner of table
   workflow_outbox"*. Migration 034 set SECURITY DEFINER for exactly this
   reason, but ``CREATE OR REPLACE FUNCTION`` resets every attribute the new
   definition does not restate — so any later replace silently reverted it, and
   nothing re-asserted it. The security flag is now set immediately after the
   replace, in the same migration, so the two cannot drift apart again.

2. **The DEFAULT partition holds rows in the target range.** Postgres refuses to
   create a partition whose bounds cover rows the default already has:
   *"updated partition constraint for default partition would be violated by
   some row"*. This is not a transient condition — it is guaranteed the moment
   anything writes before the first month partition exists, which is what
   happened. Creating the partition therefore has to detach the default, create
   it, move the rows that now belong elsewhere, and re-attach.

   The move goes DIRECTLY into the new partition, not through the parent. The
   parents carry FORCE ROW LEVEL SECURITY, so even the owner is filtered by the
   tenant policies; an insert through the parent would move nothing and lose the
   rows to the delete. Every row in ``[start, end)`` belongs to exactly that one
   partition, so routing is not needed anyway.

3. **One failure aborted everything.** The loop had no exception handling, so
   the first error left every other table and month untouched. Each month is now
   its own plpgsql sub-block: an exception rolls that month back and warns,
   and the sweep carries on.

Also adds ``workflow_drop_old_partitions()``. Retention is the point of
partitioning here — without it this is a more complicated way to store the same
unbounded history — and dropping a partition is the cheap operation that a
DELETE over 85k+ rows is not. It is NOT scheduled by this migration; wire it up
deliberately, with a retention window someone has agreed to.

Revision ID: 052
Revises: 051
"""

from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


# The four partitioned workflow tables, as a SQL literal list. Interpolated into
# the function bodies below rather than repeated, so the sweep and the retention
# helper cannot come to disagree about which tables they cover. A fixed constant,
# never input — hence the S608 suppressions where it is used.
PARTITIONED = "'workflow_outbox','workflow_runs','workflow_run_steps','workflow_run_tokens'"


_BUILD = """
CREATE OR REPLACE FUNCTION workflow_build_partition(tbl text, start_date date) RETURNS void AS $fn$
DECLARE
    end_date date := (start_date + interval '1 month')::date;
    part_name text := tbl || '_' || to_char(start_date, 'YYYYMM');
    default_name text := tbl || '_default';
BEGIN
    IF to_regclass('public.' || quote_ident(part_name)) IS NOT NULL THEN
        RETURN;
    END IF;

    -- This block is a subtransaction: if any step fails, all of it rolls back,
    -- so the default can never be left detached by a half-finished attempt.
    BEGIN
        EXECUTE format('ALTER TABLE %I DETACH PARTITION %I', tbl, default_name);

        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
            part_name, tbl, start_date, end_date
        );

        -- Straight into the new partition, not through the parent: the parents
        -- carry FORCE ROW LEVEL SECURITY, so even the owner is filtered by the
        -- tenant policies and a move through the parent would insert nothing
        -- while the delete still removed the rows. Every row in [start, end)
        -- belongs to this one partition, so routing is not needed anyway.
        EXECUTE format(
            'WITH moved AS ('
            '  DELETE FROM %I WHERE created_at >= %L AND created_at < %L RETURNING *'
            ') INSERT INTO %I SELECT * FROM moved',
            default_name, start_date, end_date, part_name
        );

        EXECUTE format('ALTER TABLE %I ATTACH PARTITION %I DEFAULT', tbl, default_name);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', part_name);
    EXCEPTION WHEN OTHERS THEN
        -- One month that cannot be built must not cost the others, or the other
        -- three tables. Say why, and carry on.
        RAISE WARNING 'workflow_build_partition: % skipped: %', part_name, SQLERRM;
    END;
END;
$fn$ LANGUAGE plpgsql;
"""


_ENSURE = f"""
CREATE OR REPLACE FUNCTION workflow_ensure_partitions(months_ahead int DEFAULT 2) RETURNS void AS $fn$
DECLARE
    tbl text;
    m int;
    start_date date;
    default_name text;
    oldest date;
BEGIN
    -- Detaching the default takes ACCESS EXCLUSIVE on the parent. Bound the
    -- wait: a maintenance sweep must never be the reason writes pile up. On
    -- timeout the month is skipped with a warning and retried next sweep.
    PERFORM set_config('lock_timeout', '5s', true);

    FOREACH tbl IN ARRAY ARRAY[{PARTITIONED}] LOOP
        default_name := tbl || '_default';
        FOR m IN 0..months_ahead LOOP
            start_date := date_trunc('month', (now() AT TIME ZONE 'UTC' + (m || ' month')::interval))::date;
            PERFORM workflow_build_partition(tbl, start_date);
        END LOOP;

        -- Drain the default BACKWARDS too, one month per sweep.
        --
        -- Everything written before a month partition existed sits in the
        -- default, and retention can only drop a month partition — so without
        -- this the bulk of existing history stays unreclaimable and the default
        -- never shrinks. One month per sweep keeps the work bounded; repeated
        -- sweeps converge until only the current months are left.
        EXECUTE format(
            'SELECT date_trunc(''month'', min(created_at) AT TIME ZONE ''UTC'')::date FROM %I',
            default_name
        ) INTO oldest;
        IF oldest IS NOT NULL AND oldest < date_trunc('month', now() AT TIME ZONE 'UTC')::date THEN
            PERFORM workflow_build_partition(tbl, oldest);
        END IF;
    END LOOP;
END;
$fn$ LANGUAGE plpgsql;
"""  # noqa: S608


_DROP_OLD = f"""
CREATE OR REPLACE FUNCTION workflow_drop_old_partitions(months_to_keep int DEFAULT 6) RETURNS int AS $fn$
DECLARE
    cutoff date;
    part record;
    dropped int := 0;
BEGIN
    IF months_to_keep < 1 THEN
        -- A window of zero would drop the CURRENT month, which is being written
        -- to. Refuse rather than interpret it.
        RAISE EXCEPTION 'months_to_keep must be at least 1';
    END IF;

    cutoff := date_trunc('month', (now() AT TIME ZONE 'UTC') - (months_to_keep || ' month')::interval)::date;

    FOR part IN
        SELECT c.relname AS name, p.relname AS parent
        FROM pg_class c
        JOIN pg_inherits i ON i.inhrelid = c.oid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname IN ({PARTITIONED})
          -- Month partitions only. The default never matches, which is the
          -- point: dropping it would discard everything not yet migrated into
          -- a month, and there is no way to get it back.
          AND c.relname ~ ('^' || p.relname || '_[0-9]{{6}}$')
    LOOP
        IF to_date(right(part.name, 6), 'YYYYMM') < cutoff THEN
            EXECUTE format('DROP TABLE %I', part.name);
            dropped := dropped + 1;
        END IF;
    END LOOP;

    RETURN dropped;
END;
$fn$ LANGUAGE plpgsql;
"""  # noqa: S608


# Both functions do owner-only DDL on behalf of a connection that owns nothing,
# so both run as their owner. search_path is pinned per the SECURITY DEFINER
# hardening guideline — an unqualified name in a definer function is resolved in
# the CALLER's search_path otherwise.
#
# One statement per op.execute: env.py migrates over asyncpg, which sends each
# statement as a prepared statement, and Postgres rejects multiple commands in
# one of those.
_SECURITY = [
    "ALTER FUNCTION workflow_build_partition(text, date) SECURITY DEFINER",
    "ALTER FUNCTION workflow_build_partition(text, date) SET search_path = public, pg_temp",
    "ALTER FUNCTION workflow_ensure_partitions(int) SECURITY DEFINER",
    "ALTER FUNCTION workflow_ensure_partitions(int) SET search_path = public, pg_temp",
    "ALTER FUNCTION workflow_drop_old_partitions(int) SECURITY DEFINER",
    "ALTER FUNCTION workflow_drop_old_partitions(int) SET search_path = public, pg_temp",
]

# The app calls these; without EXECUTE it gets "permission denied for function".
_GRANTS = [
    "GRANT EXECUTE ON FUNCTION workflow_ensure_partitions(int) TO km_app",
    "GRANT EXECUTE ON FUNCTION workflow_drop_old_partitions(int) TO km_app",
]


def upgrade() -> None:
    op.execute(_BUILD)
    op.execute(_ENSURE)
    op.execute(_DROP_OLD)
    for stmt in _SECURITY:
        op.execute(stmt)
    for stmt in _GRANTS:
        op.execute(stmt)

    # Build this month's partitions now rather than waiting for the first sweep,
    # so the fix is observable immediately and the backfill happens once, under
    # a migration, instead of inside a request.
    op.execute("SELECT workflow_ensure_partitions(2)")


def downgrade() -> None:
    # Deliberately does NOT fold the month partitions back into the default:
    # that would rewrite every workflow row to undo a maintenance fix, and the
    # partitions are readable through the parent either way. Only the helpers
    # revert.
    op.execute("DROP FUNCTION IF EXISTS workflow_drop_old_partitions(int)")
    op.execute("DROP FUNCTION IF EXISTS workflow_build_partition(text, date)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION workflow_ensure_partitions(months_ahead int DEFAULT 2) RETURNS void AS $fn$
        DECLARE
            tbl text;
            m int;
            start_date date;
            end_date date;
            part_name text;
        BEGIN
            FOREACH tbl IN ARRAY ARRAY[
                'workflow_outbox','workflow_runs','workflow_run_steps','workflow_run_tokens'
            ] LOOP
                FOR m IN 0..months_ahead LOOP
                    start_date := date_trunc('month', (now() AT TIME ZONE 'UTC' + (m || ' month')::interval))::date;
                    end_date := (start_date + interval '1 month')::date;
                    part_name := tbl || '_' || to_char(start_date, 'YYYYMM');
                    EXECUTE format(
                        'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                        part_name, tbl, start_date, end_date
                    );
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', part_name);
                END LOOP;
            END LOOP;
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )
    op.execute("ALTER FUNCTION workflow_ensure_partitions(int) SECURITY DEFINER")
    op.execute("ALTER FUNCTION workflow_ensure_partitions(int) SET search_path = public, pg_temp")
