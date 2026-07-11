"""``/api/v1/entities`` — read the custom-entity catalog (schema).

Definition authoring (DDL) stays on the first-party admin surface; the enterprise
API exposes the catalog read-only so integrations can discover entities + fields
before reading/writing records (see ``records.py``).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.api_key import ApiKeyPrincipal, get_apikey_tenant_db, require_scope
from api.models.custom_entity import EntityDefinition
from api.repositories.custom_entity import EntityDefinitionRepository, EntityFieldRepository
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.custom_entity import EntityDefinitionRead, EntityFieldRead

router = APIRouter()


async def _read_with_fields(
    session: AsyncSession, org_id: uuid.UUID, definition: EntityDefinition
) -> EntityDefinitionRead:
    """Attach a definition's fields. Takes the already-loaded definition to avoid
    re-fetching it (the list path already has it in hand)."""
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition.id)
    return EntityDefinitionRead(
        id=definition.id,
        name=definition.name,
        slug=definition.slug,
        description=definition.description,
        is_active=definition.is_active,
        fields=[EntityFieldRead.model_validate(f) for f in fields],
    )


@router.get("", response_model=PaginatedResponse[EntityDefinitionRead])
async def list_entities(
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("entities:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[EntityDefinitionRead]:
    """List the org's entity definitions with their fields (paginated).

    Requires the ``entities:read`` scope."""
    repo = EntityDefinitionRepository(session, principal.org_id)
    items, total = await repo.list_all(offset=pagination.offset, limit=pagination.page_size)
    reads = [await _read_with_fields(session, principal.org_id, d) for d in items]
    return make_page(reads, total, pagination)


@router.get("/{slug}", response_model=EntityDefinitionRead)
async def get_entity(
    slug: str,
    principal: Annotated[ApiKeyPrincipal, Depends(require_scope("entities:read"))],
    session: Annotated[AsyncSession, Depends(get_apikey_tenant_db)],
) -> EntityDefinitionRead:
    """Fetch one entity definition (and its fields) by slug.

    Requires the ``entities:read`` scope."""
    definition = await EntityDefinitionRepository(session, principal.org_id).get_by_slug(slug)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    return await _read_with_fields(session, principal.org_id, definition)
