"""Integration tests for workflow partition maintenance (migration 052).

``workflow_ensure_partitions()`` never once succeeded before this: every stack
had four DEFAULT partitions holding every row ever written, and three separate
faults kept it that way — the function ran as INVOKER and could not create a
partition, the default already held rows in the target range so Postgres refused
the bounds, and one failure aborted the whole sweep.

None of that was visible from the application. The maintenance endpoint returned
204 either way, because the errors were inside a plpgsql function whose caller
never looked. So these tests drive the SQL directly, against a table shaped like
the real ones — including FORCE ROW LEVEL SECURITY on the parent, which is the
detail that decides whether the row move works or silently discards data.

The SQL is imported from the migration rather than restated, so a test cannot
pass against a definition the migration does not actually install.
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import AsyncGenerator
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

PROBE = "wfpart_probe"


def _migration():
    """Load migration 052 by path — `versions/` is not an importable package."""
    path = (
        pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / "052_workflow_partition_maintenance.py"
    )
    spec = importlib.util.spec_from_file_location("migration_052", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _migration()


OWNER = "wfpart_owner"


@pytest_asyncio.fixture
async def probe(engine: AsyncEngine) -> AsyncGenerator[None]:
    """A partitioned table shaped like the workflow tables, with rows already in
    its default — which is the state every real stack was in.

    Owned by a NON-SUPERUSER role, and the function is SECURITY DEFINER owned by
    that same role. Both details are load-bearing. A superuser bypasses row
    security outright, so a test running as one cannot reproduce the trap that
    FORCE ROW LEVEL SECURITY sets for the owner — the row move would appear to
    work no matter how it was written.
    """
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {PROBE} CASCADE"))
        await conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = "
                f"'{OWNER}') THEN CREATE ROLE {OWNER} NOSUPERUSER; END IF; END $$"
            )
        )
        await conn.execute(text(f"GRANT CREATE, USAGE ON SCHEMA public TO {OWNER}"))
        await conn.execute(
            text(
                f"CREATE TABLE {PROBE} (id bigserial, created_at timestamptz NOT NULL, "
                f"note text, PRIMARY KEY (id, created_at)) PARTITION BY RANGE (created_at)"
            )
        )
        await conn.execute(text(f"CREATE TABLE {PROBE}_default PARTITION OF {PROBE} DEFAULT"))
        await conn.execute(text(f"ALTER TABLE {PROBE} OWNER TO {OWNER}"))
        await conn.execute(text(f"ALTER TABLE {PROBE}_default OWNER TO {OWNER}"))
        # The real parents force RLS, so even the owner is filtered by the tenant
        # policies. That is what makes moving rows THROUGH the parent lose them.
        await conn.execute(text(f"ALTER TABLE {PROBE} ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text(f"ALTER TABLE {PROBE} FORCE ROW LEVEL SECURITY"))
        await conn.execute(text(f"CREATE POLICY deny_all ON {PROBE} USING (false) WITH CHECK (false)"))
        await conn.execute(text(MIGRATION._BUILD))
        await conn.execute(text(MIGRATION._DROP_OLD))
        await conn.execute(text(f"ALTER FUNCTION workflow_build_partition(text, date) OWNER TO {OWNER}"))
        for stmt in MIGRATION._SECURITY[:2]:
            await conn.execute(text(stmt))
        for month, count in (("2026-06", 5), ("2026-07", 3)):
            for day in range(1, count + 1):
                await conn.execute(
                    text(f"INSERT INTO {PROBE}_default (created_at, note) VALUES (:ts, :n)"),  # noqa: S608
                    {"ts": datetime.fromisoformat(f"{month}-{day:02d}T12:00:00+00:00"), "n": month},
                )
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {PROBE} CASCADE"))


async def _count(engine: AsyncEngine, table: str) -> int:
    # `table` is a name this module chose, never input. Postgres has no bind
    # parameter for an identifier, so a test that counts rows in a partition it
    # just built has to interpolate the name.
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()  # noqa: S608


async def _build(engine: AsyncEngine, month: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            # `cast(... AS date)` rather than `::date`: SQLAlchemy's `text()` reads
            # the second colon of `:d::date` as part of the bind name. asyncpg then
            # wants a real date for a date parameter, not an ISO string.
            text("SELECT workflow_build_partition(:t, cast(:d AS date))"),
            {"t": PROBE, "d": date.fromisoformat(month)},
        )


class TestBuildPartition:
    async def test_creates_the_partition_even_though_the_default_holds_its_rows(self, engine: AsyncEngine, probe: None):
        """The regression. Postgres refuses `CREATE TABLE ... PARTITION OF` whose
        bounds cover rows the default already has, which on a live stack is
        always — so the naive version could never make the first partition."""
        await _build(engine, "2026-06-01")
        async with engine.connect() as conn:
            exists = (await conn.execute(text(f"SELECT to_regclass('public.{PROBE}_202606')"))).scalar_one()
        assert exists is not None

    async def test_moves_that_months_rows_into_it(self, engine: AsyncEngine, probe: None):
        await _build(engine, "2026-06-01")
        assert await _count(engine, f"{PROBE}_202606") == 5

    async def test_leaves_other_months_in_the_default(self, engine: AsyncEngine, probe: None):
        await _build(engine, "2026-06-01")
        assert await _count(engine, f"{PROBE}_default") == 3

    async def test_loses_no_rows(self, engine: AsyncEngine, probe: None):
        """The failure this guards against is silent and unrecoverable: moving
        rows through the RLS-forced parent inserts nothing while the delete still
        removes them, so the count would drop from 8 to 3 with no error."""
        before = await _count(engine, PROBE)
        await _build(engine, "2026-06-01")
        moved = await _count(engine, f"{PROBE}_202606")
        left = await _count(engine, f"{PROBE}_default")
        assert (moved, left, moved + left) == (5, 3, before)

    async def test_reattaches_the_default(self, engine: AsyncEngine, probe: None):
        """A half-finished build must not leave the default detached — writes
        outside every month bound would then fail outright."""
        await _build(engine, "2026-06-01")
        async with engine.connect() as conn:
            attached = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                        "JOIN pg_class p ON p.oid = i.inhparent "
                        "WHERE p.relname = :parent AND c.relname = :child"
                    ),
                    {"parent": PROBE, "child": f"{PROBE}_default"},
                )
            ).scalar_one()
        assert attached == 1

    async def test_is_idempotent(self, engine: AsyncEngine, probe: None):
        """The sweep runs on a schedule, so building an existing month has to be
        a no-op rather than an error that aborts the rest of it."""
        await _build(engine, "2026-06-01")
        await _build(engine, "2026-06-01")
        assert await _count(engine, f"{PROBE}_202606") == 5

    async def test_a_month_with_no_rows_still_gets_its_partition(self, engine: AsyncEngine, probe: None):
        """Pre-creating ahead of time is the normal case — nothing has been
        written to next month yet."""
        await _build(engine, "2026-09-01")
        assert await _count(engine, f"{PROBE}_202609") == 0


class TestRetention:
    async def test_refuses_a_window_that_would_drop_the_current_month(self, engine: AsyncEngine, probe: None):
        # Zero months kept would delete the partition being written to right now.
        with pytest.raises(Exception, match="at least 1"):
            async with engine.begin() as conn:
                await conn.execute(text("SELECT workflow_drop_old_partitions(0)"))

    async def test_keeps_everything_inside_the_window(self, engine: AsyncEngine, probe: None):
        async with engine.begin() as conn:
            dropped = (await conn.execute(text("SELECT workflow_drop_old_partitions(240)"))).scalar_one()
        assert dropped == 0


class TestSecurityAttributes:
    """Fault #1 was a SECURITY DEFINER flag that migration 034 set and a later
    `CREATE OR REPLACE FUNCTION` silently reset — the attribute is not carried
    over unless the new definition restates it. Nothing noticed for months."""

    @pytest.mark.parametrize(
        "signature",
        ["workflow_build_partition(text, date)", "workflow_ensure_partitions(int)"],
    )
    def test_the_migration_reasserts_definer_after_replacing_the_function(self, signature):
        assert f"ALTER FUNCTION {signature} SECURITY DEFINER" in MIGRATION._SECURITY

    @pytest.mark.parametrize(
        "signature",
        ["workflow_build_partition(text, date)", "workflow_ensure_partitions(int)"],
    )
    def test_and_pins_the_search_path(self, signature):
        # An unqualified name in a definer function resolves in the CALLER's
        # search_path, which is how a definer function becomes a way in.
        assert f"ALTER FUNCTION {signature} SET search_path = public, pg_temp" in MIGRATION._SECURITY

    async def test_the_installed_function_runs_as_its_owner(self, engine: AsyncEngine, probe: None):
        async with engine.begin() as conn:
            definer = (
                await conn.execute(text("SELECT prosecdef FROM pg_proc WHERE proname = 'workflow_build_partition'"))
            ).scalar_one()
        assert definer is True
