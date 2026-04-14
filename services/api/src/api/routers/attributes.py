"""Document attribute definition routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.repositories.attribute import AttributeDefinitionRepository
from api.schemas.attribute import (
    AttributeDefinitionCreate,
    AttributeDefinitionRead,
    AttributeDefinitionUpdate,
)

router = APIRouter()


@router.get("/", response_model=list[AttributeDefinitionRead])
async def list_attributes(
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[AttributeDefinitionRead]:
    repo = AttributeDefinitionRepository(session)
    items = await repo.list_all()
    return [AttributeDefinitionRead.model_validate(i) for i in items]


@router.get("/{attribute_id}", response_model=AttributeDefinitionRead)
async def get_attribute(
    attribute_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AttributeDefinitionRead:
    repo = AttributeDefinitionRepository(session)
    instance = await repo.get(attribute_id)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return AttributeDefinitionRead.model_validate(instance)


@router.post(
    "/", response_model=AttributeDefinitionRead, status_code=status.HTTP_201_CREATED
)
async def create_attribute(
    body: AttributeDefinitionCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AttributeDefinitionRead:
    repo = AttributeDefinitionRepository(session)
    instance = await repo.create(
        name=body.name,
        slug=body.slug,
        org_id=ctx.org_id,
        attribute_type=body.attribute_type,
        picklist_options=body.picklist_options,
        required=body.required,
        order=body.order,
    )
    return AttributeDefinitionRead.model_validate(instance)


@router.patch("/{attribute_id}", response_model=AttributeDefinitionRead)
async def update_attribute(
    attribute_id: uuid.UUID,
    body: AttributeDefinitionUpdate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> AttributeDefinitionRead:
    repo = AttributeDefinitionRepository(session)
    instance = await repo.get(attribute_id)
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    instance = await repo.update(
        instance,
        name=body.name,
        attribute_type=body.attribute_type,
        picklist_options=body.picklist_options,
        required=body.required,
        order=body.order,
    )
    return AttributeDefinitionRead.model_validate(instance)


@router.delete("/{attribute_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attribute(
    attribute_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    repo = AttributeDefinitionRepository(session)
    deleted = await repo.delete(attribute_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
