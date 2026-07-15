"""Custom-entity definition (schema) management routes.

These endpoints mutate the catalog *and* run physical DDL, so they use the
privileged ``get_db`` session (``app_user`` cannot run DDL) and require org
admin. Record CRUD lives in ``entity_records.py`` and runs under RLS.
"""

from __future__ import annotations

import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_db
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.schemas.common import PaginatedResponse, PaginationParams, make_page
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityDefinitionRead,
    EntityDefinitionUpdate,
    EntityFieldCreate,
    EntityFieldRead,
    EntityFieldUpdate,
    EntityRelationshipCreate,
    EntityRelationshipRead,
)
from api.services.entity_service import (
    EntityConflictError,
    EntityError,
    EntityLimitError,
    EntityNotFoundError,
    EntityService,
    EntityValidationError,
)

router = APIRouter()

_ERROR_STATUS = {
    EntityConflictError: status.HTTP_409_CONFLICT,
    EntityLimitError: status.HTTP_409_CONFLICT,
    EntityNotFoundError: status.HTTP_404_NOT_FOUND,
    EntityValidationError: status.HTTP_400_BAD_REQUEST,
}


def _raise_http(exc: Exception) -> NoReturn:
    code = _ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=code, detail=str(exc)) from exc


async def _read_with_fields(
    session: AsyncSession, org_id: uuid.UUID, definition_id: uuid.UUID
) -> EntityDefinitionRead:
    defs = EntityDefinitionRepository(session, org_id)
    definition = await defs.get(definition_id)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    fields = await EntityFieldRepository(session, org_id).list_for_definition(definition_id)
    return EntityDefinitionRead(
        id=definition.id,
        name=definition.name,
        slug=definition.slug,
        description=definition.description,
        is_active=definition.is_active,
        write_access=definition.write_access,
        fields=[EntityFieldRead.model_validate(f) for f in fields],
    )


@router.get("/", response_model=PaginatedResponse[EntityDefinitionRead])
async def list_definitions(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
) -> PaginatedResponse[EntityDefinitionRead]:
    repo = EntityDefinitionRepository(session, ctx.org_id)
    items, total = await repo.list_all(offset=pagination.offset, limit=pagination.page_size)
    reads = [await _read_with_fields(session, ctx.org_id, d.id) for d in items]
    return make_page(reads, total, pagination)


@router.post("/", response_model=EntityDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_definition(
    body: EntityDefinitionCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EntityDefinitionRead:
    service = EntityService(session, ctx.org_id)
    try:
        definition = await service.create_definition(body)
    except EntityError as exc:
        _raise_http(exc)
    return await _read_with_fields(session, ctx.org_id, definition.id)


@router.get("/{definition_id}", response_model=EntityDefinitionRead)
async def get_definition(
    definition_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EntityDefinitionRead:
    return await _read_with_fields(session, ctx.org_id, definition_id)


@router.patch("/{definition_id}", response_model=EntityDefinitionRead)
async def update_definition(
    definition_id: uuid.UUID,
    body: EntityDefinitionUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EntityDefinitionRead:
    repo = EntityDefinitionRepository(session, ctx.org_id)
    definition = await repo.get(definition_id)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    await repo.update(
        definition,
        name=body.name,
        description=body.description,
        is_active=body.is_active,
        write_access=body.write_access,
    )
    return await _read_with_fields(session, ctx.org_id, definition_id)


@router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_definition(
    definition_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
    force: Annotated[bool, Query()] = False,
) -> None:
    service = EntityService(session, ctx.org_id)
    try:
        await service.drop_definition(definition_id, force=force)
    except EntityError as exc:
        _raise_http(exc)


@router.post("/{definition_id}/fields", response_model=EntityFieldRead, status_code=status.HTTP_201_CREATED)
async def add_field(
    definition_id: uuid.UUID,
    body: EntityFieldCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EntityFieldRead:
    service = EntityService(session, ctx.org_id)
    try:
        field = await service.add_field(definition_id, body)
    except EntityError as exc:
        _raise_http(exc)
    return EntityFieldRead.model_validate(field)


@router.patch("/{definition_id}/fields/{field_id}", response_model=EntityFieldRead)
async def update_field(
    definition_id: uuid.UUID,
    field_id: uuid.UUID,
    body: EntityFieldUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EntityFieldRead:
    """Update a field's catalog attributes (name, picklist options, order, and
    ``read_access`` — toggling a field server-only). Type/required/slug changes
    need a physical migration and are rejected here."""
    service = EntityService(session, ctx.org_id)
    try:
        field = await service.update_field(definition_id, field_id, body)
    except EntityError as exc:
        _raise_http(exc)
    return EntityFieldRead.model_validate(field)


@router.delete("/{definition_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    definition_id: uuid.UUID,
    field_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Drop a scalar field and its physical column (destructive — data is lost)."""
    service = EntityService(session, ctx.org_id)
    try:
        await service.drop_field(definition_id, field_id)
    except EntityError as exc:
        _raise_http(exc)


@router.get("/{definition_id}/relationships", response_model=list[EntityRelationshipRead])
async def list_relationships(
    definition_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[EntityRelationshipRead]:
    rels = await EntityRelationshipRepository(session, ctx.org_id).list_for_source(definition_id)
    return [EntityRelationshipRead.model_validate(r) for r in rels]


@router.get("/{definition_id}/incoming-relationships", response_model=list[EntityRelationshipRead])
async def list_incoming_relationships(
    definition_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[EntityRelationshipRead]:
    """Relationships from other entities that TARGET this one — the child→parent
    links that drive a form's 1:M (table) sections."""
    rels = await EntityRelationshipRepository(session, ctx.org_id).list_targeting(definition_id)
    return [EntityRelationshipRead.model_validate(r) for r in rels]


@router.post(
    "/{definition_id}/relationships",
    response_model=EntityRelationshipRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_relationship(
    definition_id: uuid.UUID,
    body: EntityRelationshipCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> EntityRelationshipRead:
    service = EntityService(session, ctx.org_id)
    try:
        rel = await service.create_relationship(definition_id, body)
    except EntityError as exc:
        _raise_http(exc)
    return EntityRelationshipRead.model_validate(rel)


