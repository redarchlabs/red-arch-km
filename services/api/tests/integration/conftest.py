"""Shared fixtures for integration tests using a real PostgreSQL via testcontainers.

These tests exercise the RLS policies, which cannot be meaningfully tested
against SQLite or an in-memory store. A throwaway PostgreSQL container is
started once per test session; schema is created from the SQLAlchemy metadata
and RLS policies are applied to mirror the Alembic migration.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator

import api.models  # noqa: F401 — register all models with Base.metadata
import pytest
import pytest_asyncio
from api.models.base import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

_RLS_TABLES = [
    "regions",
    "departments",
    "roles",
    "groups",
    "folders",
    "tags",
    "documents",
    "document_access",
    "document_attribute_definitions",
    "chat_sessions",
    "user_org_memberships",
    "entity_definitions",
    "entity_fields",
    "entity_relationships",
    "workflows",
    "workflow_versions",
    "workflow_outbox",
    "workflow_runs",
    "workflow_run_steps",
    "workflow_run_tokens",
    "workflow_connections",
    "workflow_inbound_endpoints",
]


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer]:
    # Matches docker-compose.infra.yml and the CI service container.
    with PostgresContainer("postgres:18", driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    url = postgres_container.get_connection_url()
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


async def _ensure_app_user_role(engine: AsyncEngine) -> None:
    """CREATE ROLE doesn't support IF NOT EXISTS; wrap in DO block."""
    stmt = text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user;
            END IF;
        END $$;
    """)
    async with engine.begin() as conn:
        await conn.execute(stmt)


async def _enable_rls(engine: AsyncEngine) -> None:
    """Enable RLS + tenant_isolation policies on tenant-scoped tables.

    The policy expression MUST mirror the hardened production policy from Alembic
    migration 002 (RED-3): the tenant GUC is normalised with ``nullif(..., '')``
    before the uuid cast so an unset/empty tenant context returns empty (fail
    closed) instead of raising ``invalid input syntax for type uuid``.
    """
    async with engine.begin() as conn:
        for tbl in _RLS_TABLES:
            await conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
            await conn.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
            for action, clause in (
                ("select", "USING"),
                ("delete", "USING"),
                ("update", "USING"),
                ("insert", "WITH CHECK"),
            ):
                await conn.execute(
                    text(f"""
                    CREATE POLICY tenant_isolation_{action} ON {tbl}
                    FOR {action.upper()}
                    {clause} (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
                """)
                )


async def _grant_app_user(engine: AsyncEngine) -> None:
    """Grant app_user the privileges it needs to exercise the schema.

    app_user is a non-superuser, non-BYPASSRLS role, so RLS policies are
    enforced against it (the testcontainers default login is a superuser and
    would bypass RLS entirely). The enforcement `session` fixture runs as this
    role. Granted after the schema + policies exist so ALL TABLES/SEQUENCES is
    complete.
    """
    async with engine.begin() as conn:
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
        await conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user"))
        await conn.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user"))


@pytest_asyncio.fixture(scope="session")
async def engine(database_url: str) -> AsyncGenerator[AsyncEngine]:
    os.environ["DATABASE_URL"] = database_url
    # Mirror the app engine's Decimal/datetime-safe JSONB serializer so writes of
    # numeric entity fields (e.g. a calculated total) into workflow_outbox.after_data
    # don't raise 'Decimal is not JSON serializable' in tests.
    from api.db import json_serializer

    engine = create_async_engine(database_url, echo=False, json_serializer=json_serializer)

    await _ensure_app_user_role(engine)

    async with engine.begin() as conn:
        # Migration 010 enables this in real deployments; the test schema is
        # built with create_all (no migrations), so provision it here too. It
        # backs the trigram indexes SchemaManager creates for record search.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)

    await _enable_rls(engine)
    await _grant_app_user(engine)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Per-test enforcement session, run as the non-superuser ``app_user``.

    The testcontainers default login is a PostgreSQL superuser, which bypasses
    RLS even with FORCE ROW LEVEL SECURITY — so a session left as that role
    would make the isolation assertions vacuously pass. ``SET ROLE app_user``
    drops to a non-superuser role against which the policies are enforced;
    ``RESET ROLE`` on teardown keeps the pooled connection clean. Always starts
    with no tenant context set.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("SET ROLE app_user"))
        yield session
        await session.rollback()
        await session.execute(text("RESET ROLE"))


@pytest_asyncio.fixture
async def admin_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """A privileged seeding session, independent of the enforcement session.

    ``RESET ROLE`` guarantees this session runs as the superuser login role even
    if a pooled connection was left as ``app_user`` by a prior enforcement
    ``session`` (``SET ROLE app_user`` is not undone by rollback). Without this,
    a leaked role turns admin_session into an RLS-enforced session with no tenant
    context — reads silently return empty, ordering-dependent. This mirrors the
    privileged ``get_db`` connection used in production for DDL/cross-org work.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text("RESET ROLE"))
        yield session
        await session.rollback()
