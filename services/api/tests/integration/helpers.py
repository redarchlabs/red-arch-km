"""Test helpers importable by integration tests."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant(session: AsyncSession, tenant_id: str | None) -> None:
    """Set (or clear) the `app.current_tenant_id` session variable.

    Passing None clears the setting with RESET so RLS policies see an
    unset (NULL) value. Since the RED-3 hardening (migration 002) the policy
    normalises an empty GUC with `nullif(current_setting(...), '')::uuid`, so a
    literal '' now fails closed (empty result) rather than raising
    "invalid input syntax for type uuid"; RESET is still used here as the
    cleanest way to model a genuinely-absent tenant context.
    """
    if tenant_id is None:
        await session.execute(text("RESET app.current_tenant_id"))
    else:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
