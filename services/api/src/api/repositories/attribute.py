"""Repository for document attribute definitions."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.document import DocumentAttributeDefinition


class AttributeDefinitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, definition_id: uuid.UUID) -> DocumentAttributeDefinition | None:
        return await self._session.get(DocumentAttributeDefinition, definition_id)

    async def list_all(self) -> list[DocumentAttributeDefinition]:
        result = await self._session.execute(
            select(DocumentAttributeDefinition).order_by(
                DocumentAttributeDefinition.order,
                DocumentAttributeDefinition.name,
            )
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        slug: str,
        org_id: uuid.UUID,
        attribute_type: str = "freeform",
        picklist_options: list[str] | None = None,
        required: bool = False,
        order: int = 0,
    ) -> DocumentAttributeDefinition:
        instance = DocumentAttributeDefinition(
            name=name,
            slug=slug,
            org_id=org_id,
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
