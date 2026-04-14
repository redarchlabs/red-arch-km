"""Shared fixtures for integration tests using a real PostgreSQL via testcontainers.

These tests exercise the RLS policies, which cannot be meaningfully tested
against SQLite or an in-memory store. A throwaway PostgreSQL container is
started once per test session; schema is created from the SQLAlchemy metadata
and RLS policies are applied to mirror the Alembic migration.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

import api.models  # noqa: F401 — register all models with Base.metadata
from api.models.base import Base

_RLS_TABLES = [
    "regions", "departments", "roles", "groups",
    "folders", "tags", "documents", "document_access",
    "document_attribute_definitions", "chat_sessions",
    "user_org_memberships",
]


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer]:
    with PostgresContainer("postgres:16.4", driver="asyncpg") as pg:
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
    """Enable RLS + tenant_isolation policies on tenant-scoped tables."""
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
                await conn.execute(text(f"""
                    CREATE POLICY tenant_isolation_{action} ON {tbl}
                    FOR {action.upper()}
                    {clause} (org_id = current_setting('app.current_tenant_id', true)::uuid)
                """))


@pytest_asyncio.fixture(scope="session")
async def engine(database_url: str) -> AsyncGenerator[AsyncEngine]:
    os.environ["DATABASE_URL"] = database_url
    engine = create_async_engine(database_url, echo=False)

    await _ensure_app_user_role(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _enable_rls(engine)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Per-test session; always starts with no tenant context set."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def admin_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """A second session used for seeding, independent of the test session."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
