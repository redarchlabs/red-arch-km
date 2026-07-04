"""Harden RLS tenant-isolation policies to fail closed on an empty tenant GUC (RED-3).

The initial policies (migration 001) scoped rows with a bare cast::

    org_id = current_setting('app.current_tenant_id', true)::uuid

`current_setting(name, true)` returns the *empty string* ``''`` (not NULL) when
the custom GUC has been defined-then-reverted on a connection — which is exactly
what happens on a pooled connection after a prior request set the tenant with
``set_config(..., is_local => true)`` and the transaction ended. Casting
``''::uuid`` raises ``invalid input syntax for type uuid`` on the *next* query
that touches an RLS table. That is fail-closed (no cross-tenant leak) but noisy:
it turns a benign "no tenant context" state into a 500 instead of an empty result.

This migration rewrites every ``tenant_isolation_*`` policy to normalise the empty
string to NULL before casting::

    org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid

``nullif('', '')`` -> NULL, ``NULL::uuid`` -> NULL, and ``org_id = NULL`` is NULL
(never true), so an unset/empty tenant deterministically returns zero rows for
SELECT and blocks every INSERT/UPDATE/DELETE — fail-closed *and* error-free.

Revision ID: 002
Revises: 001
Create Date: 2026-07-04
"""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

# Must stay in sync with the _RLS_TABLES list in migration 001 and the test
# conftest. Every tenant-scoped table carries an org_id column and RLS.
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
]

# Hardened expression: empty-string GUC normalised to NULL before the uuid cast.
_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
# Original (pre-RED-3) expression: bare cast that throws on an empty GUC.
_ORIGINAL = "org_id = current_setting('app.current_tenant_id', true)::uuid"

# (policy suffix, action keyword, using/with-check clause)
_POLICIES = [
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
]


def _recreate_policies(expr: str) -> None:
    """Drop and recreate all tenant_isolation policies using ``expr``.

    Postgres has no ``ALTER POLICY ... USING`` that lets us swap the expression
    in place across both USING and WITH CHECK cleanly, so we drop+recreate. RLS
    stays ENABLED/FORCED throughout (we never disable it), so there is no window
    where the tables are unprotected.
    """
    for table in _RLS_TABLES:
        for suffix, action, clause in _POLICIES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON {table}")
            op.execute(f"CREATE POLICY tenant_isolation_{suffix} ON {table} FOR {action} {clause} ({expr})")


def upgrade() -> None:
    _recreate_policies(_HARDENED)


def downgrade() -> None:
    _recreate_policies(_ORIGINAL)
