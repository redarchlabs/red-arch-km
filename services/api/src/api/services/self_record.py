"""Resolve the current user's own record in an entity by matching their email.

Shared identity rule for two "it's me" features so they can't drift apart:

* the view renderer's ``record_id=me`` sentinel (auto-bind a view to the caller's
  own record), and
* the record-list ``@me`` filter sentinel (scope a live board to the caller's
  own rows).

Both match a configurable identity field (default ``email``) case-insensitively.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from api import db_scope
from api.repositories.custom_entity import EntityDefinitionRepository, EntityFieldRepository
from api.repositories.dynamic_entity import DynamicEntityRepository

# Field slug matched against the current user's email to find their own record.
IDENTITY_FIELD_SLUG = "email"


async def resolve_own_record_id(
    session: AsyncSession,
    org_id: uuid.UUID,
    entity_definition_id: uuid.UUID,
    email: str,
    *,
    field_slug: str = IDENTITY_FIELD_SLUG,
) -> uuid.UUID | None:
    """Resolve the caller to their own record in ``entity_definition_id`` by matching
    ``field_slug`` (default ``email``) to ``email`` case-insensitively.

    Returns the matching record id, or ``None`` when the entity has no such field or
    no record matches — the caller then falls back to an unbound render / an
    empty result rather than erroring.
    """
    definition = await EntityDefinitionRepository(session, org_id).get(entity_definition_id)
    if definition is None:
        return None
    fields = await EntityFieldRepository(session, org_id).list_for_definition(entity_definition_id)
    if not any(f.slug == field_slug for f in fields):
        return None
    # Scope to the org's RLS tenant BEFORE reading records: a session with no tenant
    # GUC fails closed (RLS → zero rows). enter_tenant is a transaction-scoped
    # SET LOCAL and idempotent, so an outer/inner re-pin is a no-op.
    await db_scope.enter_tenant(session, org_id)
    # Relationships are irrelevant to a scalar-field lookup, so skip loading them.
    repo = DynamicEntityRepository(session, org_id, definition, fields)
    record = await repo.find_one_by_field(field_slug, email, case_insensitive=True)
    return uuid.UUID(str(record["id"])) if record is not None else None
