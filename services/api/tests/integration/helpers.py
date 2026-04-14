"""Test helpers importable by integration tests."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant(session: AsyncSession, tenant_id: str | None) -> None:
    """Set (or clear) the `app.current_tenant_id` session variable.

    Passing None clears the setting so RLS policies treat the session as
    having no tenant context (most queries will then return empty results).
    """
    if tenant_id is None:
        await session.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
    else:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
