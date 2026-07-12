"""Transaction-scoped RLS session helpers — the ``SET LOCAL`` dance in one place.

KM2 enforces tenant isolation with ``FORCE ROW LEVEL SECURITY``. The app connects
as a **non-superuser role** (``km_app``) that is fully subject to RLS, so there
are two explicit access modes:

* **Tenant-scoped** (the default for org requests and per-event sweep work):
  drop to ``app_user`` and pin ``app.current_tenant_id``. RLS scopes every
  statement to that one org, and it is a real backstop even if application-level
  ``org_id`` filtering had a bug.
* **Bypass** (the privileged cross-org / no-tenant-context paths — ``get_db``,
  the workflow + agent poll sweeps' cross-org *claims*, provisioning, site-admin,
  token→org lookups): set ``app.bypass = 'on'``, which the permissive
  ``admin_bypass_all`` policy (migration 034) honors, widening visibility to
  every org for reads **and** writes.

This replaces the previous model where the base connection role was the Postgres
superuser (BYPASSRLS). The security posture is identical — the ``get_db`` vs
``get_tenant_db`` split is preserved — but it is enforced by a GUC + policy
instead of a connection-role attribute, so it runs on Google Cloud SQL (whose
``cloudsqlsuperuser`` cannot hold ``BYPASSRLS``).

Everything uses ``SET LOCAL`` / ``set_config(..., true)``, so it is
transaction-scoped and auto-reverts on commit/rollback (and on savepoint
rollback) — pooled connections stay clean. The GUC defaults to unset (``''`` →
not ``'on'``), so a session that sets *neither* mode fails closed (RLS with no
tenant GUC returns zero rows) — the safe default.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def enter_bypass(session: AsyncSession) -> None:
    """Opt into the cross-org RLS bypass for the rest of this transaction.

    Use for deliberately-privileged work: cross-org reads/writes and
    no-tenant-context statements (the sweep *claims*, ``get_db``, token lookups).
    """
    await session.execute(text("SET LOCAL app.bypass = 'on'"))


async def enter_tenant(session: AsyncSession, org_id: uuid.UUID | str) -> None:
    """Scope the rest of this transaction to a single org.

    Drops to the non-privileged ``app_user`` role, forces the bypass GUC **off**
    (so a stale ``'on'`` from an enclosing scope can never widen this unit), and
    pins ``app.current_tenant_id`` so the tenant policies enforce. Callers that
    also want request hardening (UTC timezone, statement timeout) add it after.
    """
    await session.execute(text("SET LOCAL ROLE app_user"))
    await session.execute(text("SET LOCAL app.bypass = 'off'"))
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(org_id)},
    )


async def enter_tenant_owner(session: AsyncSession, org_id: uuid.UUID | str) -> None:
    """Scope to one org while staying on the base ``km_app`` role (not app_user).

    Same isolation as :func:`enter_tenant` — ``km_app`` is a non-superuser, so
    RLS still enforces and the tenant GUC scopes every statement — but it keeps
    ``km_app``'s ``CREATE`` privilege so paths that run DDL for the org can work
    (the agent executor and the config assistant author ``ce_*`` entity tables).
    Use this instead of :func:`enter_tenant` only when the unit legitimately runs
    DDL; otherwise prefer the least-privilege ``app_user`` scope.
    """
    await session.execute(text("RESET ROLE"))
    await session.execute(text("SET LOCAL app.bypass = 'off'"))
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(org_id)},
    )


async def exit_to_bypass(session: AsyncSession) -> None:
    """Return from a per-tenant unit to privileged cross-org mode.

    Restores the base connection role and re-enables the bypass GUC so the next
    cross-org claim / bookkeeping statement runs unscoped again. ``enter_tenant``
    always re-pins the tenant GUC for the next unit, so a stale value cannot leak.
    """
    await session.execute(text("RESET ROLE"))
    await session.execute(text("SET LOCAL app.bypass = 'on'"))
