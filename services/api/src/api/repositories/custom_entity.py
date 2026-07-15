"""Repositories for the custom-entity catalog.

Tenant-bound like ``AttributeDefinitionRepository``: every query is explicitly
scoped to ``org_id`` (belt-and-suspenders alongside RLS). These operate on
either the privileged ``get_db`` session (catalog writes that accompany DDL) or
a ``get_tenant_db`` session (reads), because they never rely on RLS alone.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.custom_entity import EntityDefinition, EntityField, EntityRelationship


class EntityDefinitionRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, definition_id: uuid.UUID) -> EntityDefinition | None:
        result = await self._session.execute(
            select(EntityDefinition).where(
                EntityDefinition.id == definition_id,
                EntityDefinition.org_id == self._org_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> EntityDefinition | None:
        result = await self._session.execute(
            select(EntityDefinition).where(
                EntityDefinition.slug == slug,
                EntityDefinition.org_id == self._org_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self, *, offset: int = 0, limit: int = 200) -> tuple[list[EntityDefinition], int]:
        base = select(EntityDefinition).where(EntityDefinition.org_id == self._org_id)
        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self._session.execute(
            base.order_by(EntityDefinition.name).offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def count(self) -> int:
        return (
            await self._session.execute(
                select(func.count()).select_from(
                    select(EntityDefinition).where(EntityDefinition.org_id == self._org_id).subquery()
                )
            )
        ).scalar_one()

    async def create(
        self,
        *,
        definition_id: uuid.UUID,
        name: str,
        slug: str,
        physical_table: str,
        description: str | None = None,
        write_access: str = "member",
    ) -> EntityDefinition:
        instance = EntityDefinition(
            id=definition_id,
            name=name,
            slug=slug,
            physical_table=physical_table,
            description=description,
            write_access=write_access,
            org_id=self._org_id,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def update(
        self,
        definition: EntityDefinition,
        *,
        name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
        write_access: str | None = None,
    ) -> EntityDefinition:
        if name is not None:
            definition.name = name
        if description is not None:
            definition.description = description
        if is_active is not None:
            definition.is_active = is_active
        if write_access is not None:
            definition.write_access = write_access
        await self._session.flush()
        return definition

    async def delete(self, definition: EntityDefinition) -> None:
        await self._session.delete(definition)
        await self._session.flush()


class EntityFieldRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_for_definition(self, definition_id: uuid.UUID) -> list[EntityField]:
        result = await self._session.execute(
            select(EntityField)
            .where(
                EntityField.entity_definition_id == definition_id,
                EntityField.org_id == self._org_id,
            )
            .order_by(EntityField.order, EntityField.name)
        )
        return list(result.scalars().all())

    async def get(self, field_id: uuid.UUID) -> EntityField | None:
        result = await self._session.execute(
            select(EntityField).where(
                EntityField.id == field_id,
                EntityField.org_id == self._org_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        field_id: uuid.UUID,
        definition_id: uuid.UUID,
        name: str,
        slug: str,
        physical_column: str,
        field_type: str,
        picklist_options: list[str] | None = None,
        is_required: bool = False,
        is_unique: bool = False,
        default_value: Any | None = None,
        order: int = 0,
        read_access: str = "member",
    ) -> EntityField:
        instance = EntityField(
            id=field_id,
            entity_definition_id=definition_id,
            name=name,
            slug=slug,
            physical_column=physical_column,
            field_type=field_type,
            picklist_options=picklist_options or [],
            is_required=is_required,
            is_unique=is_unique,
            default_value=default_value,
            order=order,
            read_access=read_access,
            org_id=self._org_id,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def update(
        self,
        field: EntityField,
        *,
        name: str | None = None,
        picklist_options: list[str] | None = None,
        order: int | None = None,
        read_access: str | None = None,
    ) -> EntityField:
        """Update a field's catalog-only attributes (no DDL). ``is_required`` and
        type/slug changes are intentionally excluded — those need a physical
        migration and are handled elsewhere."""
        if name is not None:
            field.name = name
        if picklist_options is not None:
            field.picklist_options = picklist_options
        if order is not None:
            field.order = order
        if read_access is not None:
            field.read_access = read_access
        await self._session.flush()
        return field

    async def delete(self, field: EntityField) -> None:
        await self._session.delete(field)
        await self._session.flush()


class EntityRelationshipRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_for_source(self, definition_id: uuid.UUID) -> list[EntityRelationship]:
        result = await self._session.execute(
            select(EntityRelationship)
            .where(
                EntityRelationship.source_definition_id == definition_id,
                EntityRelationship.org_id == self._org_id,
            )
            .order_by(EntityRelationship.name)
        )
        return list(result.scalars().all())

    async def list_targeting(self, definition_id: uuid.UUID) -> list[EntityRelationship]:
        """Relationships that *point at* this definition — used to guard drops."""
        result = await self._session.execute(
            select(EntityRelationship).where(
                EntityRelationship.target_definition_id == definition_id,
                EntityRelationship.org_id == self._org_id,
            )
        )
        return list(result.scalars().all())

    async def get(self, relationship_id: uuid.UUID) -> EntityRelationship | None:
        result = await self._session.execute(
            select(EntityRelationship).where(
                EntityRelationship.id == relationship_id,
                EntityRelationship.org_id == self._org_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        relationship_id: uuid.UUID,
        source_definition_id: uuid.UUID,
        target_definition_id: uuid.UUID,
        name: str,
        slug: str,
        cardinality: str,
        on_delete: str,
        physical_name: str,
        is_required: bool = False,
    ) -> EntityRelationship:
        instance = EntityRelationship(
            id=relationship_id,
            source_definition_id=source_definition_id,
            target_definition_id=target_definition_id,
            name=name,
            slug=slug,
            cardinality=cardinality,
            on_delete=on_delete,
            physical_name=physical_name,
            is_required=is_required,
            org_id=self._org_id,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def delete(self, relationship: EntityRelationship) -> None:
        await self._session.delete(relationship)
        await self._session.flush()
