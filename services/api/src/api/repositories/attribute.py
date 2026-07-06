"""Repository for document attribute definitions."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import DocumentAttributeDefinition


class AttributeDefinitionRepository:
    """Tenant-bound repository. Every query is explicitly scoped to ``org_id``
    (belt-and-suspenders alongside RLS)."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def get(self, definition_id: uuid.UUID) -> DocumentAttributeDefinition | None:
        result = await self._session.execute(
            select(DocumentAttributeDefinition).where(
                DocumentAttributeDefinition.id == definition_id,
                DocumentAttributeDefinition.org_id == self._org_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self, *, offset: int = 0, limit: int = 200) -> tuple[list[DocumentAttributeDefinition], int]:
        from sqlalchemy import func

        base = select(DocumentAttributeDefinition).where(DocumentAttributeDefinition.org_id == self._org_id)
        total = (await self._session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self._session.execute(
            base.order_by(
                DocumentAttributeDefinition.order,
                DocumentAttributeDefinition.name,
            )
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def create(
        self,
        *,
        name: str,
        slug: str,
        attribute_type: str = "freeform",
        picklist_options: list[str] | None = None,
        required: bool = False,
        order: int = 0,
    ) -> DocumentAttributeDefinition:
        instance = DocumentAttributeDefinition(
            name=name,
            slug=slug,
            org_id=self._org_id,
            attribute_type=attribute_type,
            picklist_options=picklist_options or [],
            required=required,
            order=order,
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def update(
        self,
        definition: DocumentAttributeDefinition,
        *,
        name: str | None = None,
        attribute_type: str | None = None,
        picklist_options: list[str] | None = None,
        required: bool | None = None,
        order: int | None = None,
    ) -> DocumentAttributeDefinition:
        if name is not None:
            definition.name = name
        if attribute_type is not None:
            definition.attribute_type = attribute_type
        if picklist_options is not None:
            definition.picklist_options = picklist_options
        if required is not None:
            definition.required = required
        if order is not None:
            definition.order = order
        await self._session.flush()
        return definition

    async def delete(self, definition_id: uuid.UUID) -> bool:
        instance = await self.get(definition_id)
        if instance is None:
            return False
        await self._session.delete(instance)
        await self._session.flush()
        return True
