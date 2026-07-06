"""Orchestration for custom-entity definitions.

Ties together catalog writes (via the repositories) and physical DDL (via
``SchemaManager``) in a single unit of work on the **privileged** session, so a
DDL failure rolls back the catalog rows too (Postgres DDL is transactional).

Shared by the REST router and the AI-agent tools so both go through identical
validation, limits, and identifier derivation.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.custom_entity import EntityDefinition, EntityField, EntityRelationship
from api.repositories.custom_entity import (
    EntityDefinitionRepository,
    EntityFieldRepository,
    EntityRelationshipRepository,
)
from api.schemas.custom_entity import (
    EntityDefinitionCreate,
    EntityFieldCreate,
    EntityRelationshipCreate,
)
from api.services import identifiers
from api.services.schema_manager import SchemaManager

# Per-org safety limits (DoS guard against unbounded runtime object creation).
MAX_DEFINITIONS_PER_ORG = 200
MAX_FIELDS_PER_ENTITY = 100
MAX_RELATIONSHIPS_PER_ENTITY = 50

_TO_ONE = ("one_to_one", "many_to_one")


class EntityError(Exception):
    """Base error for entity orchestration."""


class EntityConflictError(EntityError):
    """A slug/name already exists (HTTP 409)."""


class EntityLimitError(EntityError):
    """A per-org limit was exceeded (HTTP 409)."""


class EntityNotFoundError(EntityError):
    """A referenced definition does not exist (HTTP 404)."""


class EntityValidationError(EntityError):
    """Invalid request that isn't caught by schema validation (HTTP 400)."""


class EntityService:
    """Runs on the privileged (``get_db``) session — DDL + catalog together."""

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id
        self._defs = EntityDefinitionRepository(session, org_id)
        self._fields = EntityFieldRepository(session, org_id)
        self._rels = EntityRelationshipRepository(session, org_id)
        self._schema = SchemaManager(session)

    # ------------------------------------------------------------------ #
    # Definitions
    # ------------------------------------------------------------------ #
    async def create_definition(self, body: EntityDefinitionCreate) -> EntityDefinition:
        if await self._defs.count() >= MAX_DEFINITIONS_PER_ORG:
            raise EntityLimitError(f"max {MAX_DEFINITIONS_PER_ORG} entities per org")
        if len(body.fields) > MAX_FIELDS_PER_ENTITY:
            raise EntityLimitError(f"max {MAX_FIELDS_PER_ENTITY} fields per entity")
        if await self._defs.get_by_slug(body.slug) is not None:
            raise EntityConflictError(f"entity slug already exists: {body.slug!r}")

        def_id = uuid.uuid4()
        definition = await self._defs.create(
            definition_id=def_id,
            name=body.name,
            slug=body.slug,
            physical_table=identifiers.table_name(def_id),
            description=body.description,
        )
        fields = [await self._create_field_row(def_id, f) for f in body.fields]
        await self._schema.create_entity_table(definition, fields)
        return definition

    async def _create_field_row(self, definition_id: uuid.UUID, body: EntityFieldCreate) -> EntityField:
        field_id = uuid.uuid4()
        return await self._fields.create(
            field_id=field_id,
            definition_id=definition_id,
            name=body.name,
            slug=body.slug,
            physical_column=identifiers.column_name(field_id),
            field_type=body.field_type,
            picklist_options=body.picklist_options,
            is_required=body.is_required,
            is_unique=body.is_unique,
            default_value=body.default_value,
            order=body.order,
        )

    async def add_field(self, definition_id: uuid.UUID, body: EntityFieldCreate) -> EntityField:
        definition = await self._defs.get(definition_id)
        if definition is None:
            raise EntityNotFoundError("entity not found")
        existing = await self._fields.list_for_definition(definition_id)
        if len(existing) >= MAX_FIELDS_PER_ENTITY:
            raise EntityLimitError(f"max {MAX_FIELDS_PER_ENTITY} fields per entity")
        if any(f.slug == body.slug for f in existing):
            raise EntityConflictError(f"field slug already exists: {body.slug!r}")
        # Adding a required column to a possibly-populated table needs a default;
        # keep it simple and safe: new columns are added nullable.
        if body.is_required:
            raise EntityValidationError("new fields must be added optional; set required after backfill")
        field = await self._create_field_row(definition_id, body)
        await self._schema.add_field_column(definition, field)
        return field

    async def drop_field(self, definition_id: uuid.UUID, field_id: uuid.UUID) -> None:
        """Drop a scalar field: its physical column (with data) and catalog row.

        DDL + catalog delete share one privileged transaction, so a failure in
        either rolls both back. Dropping the column cascades to any unique
        constraint / trigram index Postgres built on it.
        """
        definition = await self._defs.get(definition_id)
        if definition is None:
            raise EntityNotFoundError("entity not found")
        field = await self._fields.get(field_id)
        if field is None or field.entity_definition_id != definition_id:
            raise EntityNotFoundError("field not found")
        await self._schema.drop_field_column(definition, field)
        await self._fields.delete(field)

    async def drop_definition(self, definition_id: uuid.UUID, *, force: bool = False) -> None:
        definition = await self._defs.get(definition_id)
        if definition is None:
            raise EntityNotFoundError("entity not found")
        incoming = await self._rels.list_targeting(definition_id)
        if incoming and not force:
            raise EntityConflictError("entity is referenced by relationships; pass force=true to drop")
        # force=True with incoming references: tear each referencing relationship
        # down FIRST. Otherwise (a) DROP TABLE is rejected by the FK dependency
        # from the referencing table, and (b) deleting the catalog row raises
        # IntegrityError because entity_relationships.target_definition_id is
        # ON DELETE RESTRICT — either way an unhandled 500.
        for rel in incoming:
            await self._drop_incoming_relationship(rel)
        # Drop m2m join tables this entity owns before dropping the table itself.
        for rel in await self._rels.list_for_source(definition_id):
            if rel.cardinality == "many_to_many":
                await self._schema.drop_table_by_name(rel.physical_name)
        await self._schema.drop_entity_table(definition)
        await self._defs.delete(definition)  # cascades catalog fields + outgoing rels

    async def _drop_incoming_relationship(self, rel: EntityRelationship) -> None:
        """Remove one relationship that TARGETS the entity being dropped —
        physical object first, then its catalog row."""
        if rel.cardinality == "many_to_many":
            # The join table references this entity's table (ON DELETE CASCADE);
            # drop the whole join table.
            await self._schema.drop_table_by_name(rel.physical_name)
        else:
            # To-one: the FK column lives on the SOURCE table. Dropping the column
            # auto-drops its FK/unique constraint + index. SchemaManager.drop_field
            # _column only reads ``.physical_table`` / ``.physical_column``.
            source = await self._defs.get(rel.source_definition_id)
            if source is not None:
                await self._schema.drop_field_column(
                    source,
                    SimpleNamespace(physical_column=rel.physical_name),  # type: ignore[arg-type]
                )
        await self._rels.delete(rel)

    # ------------------------------------------------------------------ #
    # Relationships
    # ------------------------------------------------------------------ #
    async def create_relationship(
        self, source_id: uuid.UUID, body: EntityRelationshipCreate
    ) -> EntityRelationship:
        source = await self._defs.get(source_id)
        if source is None:
            raise EntityNotFoundError("source entity not found")
        # get() is org-scoped, so a target in another org resolves to None.
        target = await self._defs.get(body.target_definition_id)
        if target is None:
            raise EntityValidationError("target entity not found in this org")
        existing = await self._rels.list_for_source(source_id)
        if len(existing) >= MAX_RELATIONSHIPS_PER_ENTITY:
            raise EntityLimitError(f"max {MAX_RELATIONSHIPS_PER_ENTITY} relationships per entity")
        if any(r.slug == body.slug for r in existing):
            raise EntityConflictError(f"relationship slug already exists: {body.slug!r}")

        rel_id = uuid.uuid4()
        physical_name = (
            identifiers.join_table_name(rel_id)
            if body.cardinality == "many_to_many"
            else identifiers.relation_column_name(rel_id)
        )
        relationship = await self._rels.create(
            relationship_id=rel_id,
            source_definition_id=source_id,
            target_definition_id=body.target_definition_id,
            name=body.name,
            slug=body.slug,
            cardinality=body.cardinality,
            on_delete=body.on_delete,
            physical_name=physical_name,
            is_required=body.is_required,
        )
        await self._schema.add_relationship(source, target, relationship)
        return relationship
