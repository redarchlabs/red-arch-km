"""Test helpers importable by integration tests."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant(session: AsyncSession, tenant_id: str | None) -> None:
    """Set (or clear) the `app.current_tenant_id` session variable.

    Passing None clears the setting with RESET so RLS policies see an
    unset (NULL) value. Using set_config with an empty string would leave
    a literal '' in the setting, which the policy expression
    `current_setting(...)::uuid` would then try to cast and raise
    "invalid input syntax for type uuid" on any subsequent RLS query.
    """
    if tenant_id is None:
        await session.execute(text("RESET app.current_tenant_id"))
    else:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
