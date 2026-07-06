"""Unit tests for EntityService DoS guards + schema-time validation.

Repositories/SchemaManager are replaced with mocks so the per-org limits and
guard branches are exercised at their boundaries without creating real rows or
running DDL.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from api.services.entity_service import (
    MAX_DEFINITIONS_PER_ORG,
    MAX_FIELDS_PER_ENTITY,
    MAX_RELATIONSHIPS_PER_ENTITY,
    EntityLimitError,
    EntityNotFoundError,
    EntityService,
    EntityValidationError,
)


def _service() -> EntityService:
    svc = EntityService(MagicMock(), uuid.uuid4())
    svc._defs = AsyncMock()
    svc._fields = AsyncMock()
    svc._rels = AsyncMock()
    svc._schema = AsyncMock()
    return svc


class TestDefinitionLimits:
    async def test_definition_count_at_limit_rejected(self) -> None:
        svc = _service()
        svc._defs.count.return_value = MAX_DEFINITIONS_PER_ORG
        with pytest.raises(EntityLimitError):
            await svc.create_definition(EntityDefinitionCreate(name="X", slug="x"))

    async def test_too_many_fields_on_create_rejected(self) -> None:
        svc = _service()
        svc._defs.count.return_value = 0
        fields = [
            EntityFieldCreate(name=f"F{i}", slug=f"f{i}", field_type="text")
            for i in range(MAX_FIELDS_PER_ENTITY + 1)
        ]
        with pytest.raises(EntityLimitError):
            await svc.create_definition(EntityDefinitionCreate(name="X", slug="x", fields=fields))


class TestFieldGuards:
    async def test_add_field_at_limit_rejected(self) -> None:
        svc = _service()
        svc._defs.get.return_value = MagicMock()
        svc._fields.list_for_definition.return_value = [MagicMock() for _ in range(MAX_FIELDS_PER_ENTITY)]
        with pytest.raises(EntityLimitError):
            await svc.add_field(uuid.uuid4(), EntityFieldCreate(name="N", slug="n", field_type="text"))

    async def test_add_required_field_rejected(self) -> None:
        svc = _service()
        svc._defs.get.return_value = MagicMock()
        svc._fields.list_for_definition.return_value = []
        with pytest.raises(EntityValidationError):
            await svc.add_field(
                uuid.uuid4(),
                EntityFieldCreate(name="N", slug="n", field_type="text", is_required=True),
            )

    async def test_add_field_unknown_definition_404(self) -> None:
        svc = _service()
        svc._defs.get.return_value = None
        with pytest.raises(EntityNotFoundError):
            await svc.add_field(uuid.uuid4(), EntityFieldCreate(name="N", slug="n", field_type="text"))

    async def test_drop_field_from_other_definition_404(self) -> None:
        svc = _service()
        def_id = uuid.uuid4()
        svc._defs.get.return_value = MagicMock(id=def_id)
        # Field belongs to a *different* definition → treated as not found.
        svc._fields.get.return_value = MagicMock(entity_definition_id=uuid.uuid4())
        with pytest.raises(EntityNotFoundError):
            await svc.drop_field(def_id, uuid.uuid4())


class TestRelationshipGuards:
    async def test_relationship_at_limit_rejected(self) -> None:
        svc = _service()
        source, target = MagicMock(), MagicMock()
        svc._defs.get.side_effect = [source, target]
        svc._rels.list_for_source.return_value = [
            MagicMock() for _ in range(MAX_RELATIONSHIPS_PER_ENTITY)
        ]
        with pytest.raises(EntityLimitError):
            await svc.create_relationship(
                uuid.uuid4(),
                EntityRelationshipCreate(
                    name="R", slug="r", cardinality="many_to_one", target_definition_id=uuid.uuid4()
                ),
            )

    async def test_cross_org_target_rejected(self) -> None:
        svc = _service()
        # source resolves; target (org-scoped get) resolves to None → cross-org.
        svc._defs.get.side_effect = [MagicMock(), None]
        with pytest.raises(EntityValidationError):
            await svc.create_relationship(
                uuid.uuid4(),
                EntityRelationshipCreate(
                    name="R", slug="r", cardinality="many_to_one", target_definition_id=uuid.uuid4()
                ),
            )

    async def test_missing_source_404(self) -> None:
        svc = _service()
        svc._defs.get.side_effect = [None]
        with pytest.raises(EntityNotFoundError):
            await svc.create_relationship(
                uuid.uuid4(),
                EntityRelationshipCreate(
                    name="R", slug="r", cardinality="many_to_one", target_definition_id=uuid.uuid4()
                ),
            )
