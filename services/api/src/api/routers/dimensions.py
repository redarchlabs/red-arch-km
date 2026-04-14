"""Routes for permission dimensions: regions, departments, roles, groups.

A single router with four identical sub-resources, each backed by its own
SQLAlchemy model. This keeps the surface DRY while still giving REST-ful URLs.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth.dependencies import OrgContext, require_org_admin
from api.dependencies import get_tenant_db
from api.models.org import Department, Group, Region, Role
from api.repositories.dimension import DimensionRepository
from api.schemas.org import DimensionCreate, DimensionRead

router = APIRouter()

_DIMENSIONS = {
    "regions": Region,
    "departments": Department,
    "roles": Role,
    "groups": Group,
}


def _resolve_model(dimension: str) -> type[Region | Department | Role | Group]:
    model = _DIMENSIONS.get(dimension)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown dimension: {dimension}",
        )
    return model


@router.get("/{dimension}", response_model=list[DimensionRead])
async def list_dimensions(
    dimension: str,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[DimensionRead]:
    model = _resolve_model(dimension)
    repo = DimensionRepository(session, model)
    items = await repo.list_all()
    return [DimensionRead.model_validate(item) for item in items]


@router.post("/{dimension}", response_model=DimensionRead, status_code=status.HTTP_201_CREATED)
async def create_dimension(
    dimension: str,
    body: DimensionCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DimensionRead:
    model = _resolve_model(dimension)
    repo = DimensionRepository(session, model)
    instance = await repo.create(
        name=body.name, description=body.description, org_id=ctx.org_id
    )
    return DimensionRead.model_validate(instance)


@router.patch("/{dimension}/{dimension_id}", response_model=DimensionRead)
async def update_dimension(
    dimension: str,
    dimension_id: uuid.UUID,
    body: DimensionCreate,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> DimensionRead:
    model = _resolve_model(dimension)
    repo = DimensionRepository(session, model)
    instance = await repo.update(
        dimension_id, name=body.name, description=body.description
    )
    if instance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return DimensionRead.model_validate(instance)


@router.delete("/{dimension}/{dimension_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dimension(
    dimension: str,
    dimension_id: uuid.UUID,
    ctx: Annotated[OrgContext, Depends(require_org_admin)],
    session: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> None:
    model = _resolve_model(dimension)
    repo = DimensionRepository(session, model)
    deleted = await repo.delete(dimension_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
