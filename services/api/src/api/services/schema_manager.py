"""Runtime DDL for custom-entity physical tables.

``SchemaManager`` creates and evolves the *real* Postgres tables that back
custom entities. It must run on the privileged (``get_db``) connection — the
owner role that may run DDL and ``GRANT`` — never on ``app_user``.

Two hard safety rules (see the plan's "Guiding architectural rules"):

1. No user string is ever interpolated into DDL. Every physical identifier is
   UUID-derived and passes ``identifiers.safe_identifier`` before use.
2. Every runtime table (entity tables *and* M:M join tables) carries ``org_id``
   and the identical RLS template as the static schema, then is granted to
   ``app_user`` — so tenant isolation is enforced by the database.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql as _pg
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateIndex, CreateTable, DropTable

from api.models.custom_entity import EntityDefinition, EntityField, EntityRelationship
from api.services import identifiers

# Hardened tenant-isolation expression — identical to migration 002.
_HARDENED = "org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = (
    ("select", "SELECT", "USING"),
    ("insert", "INSERT", "WITH CHECK"),
    ("update", "UPDATE", "USING"),
    ("delete", "DELETE", "USING"),
)

_VALID_ON_DELETE = ("CASCADE", "SET NULL", "RESTRICT")

# Field types stored as text — the ones a trigram GIN index can accelerate for
# case-insensitive substring search on large tables. Kept in sync with the
# repository's search predicate.
SEARCHABLE_FIELD_TYPES = ("text", "long_text", "picklist")

# Scalar field types that benefit from a plain btree index for equality/range
# filtering, GROUP BY, and ORDER BY (the reporting/aggregation engine and
# server-side record filters). Text types already get a trigram GIN index, but
# ``picklist`` is included here too: it is short, low-cardinality, and the most
# common CRM group-by / equality filter (e.g. deal stage), which btree serves
# far better than trigram. ``boolean`` is omitted (too low-cardinality to index);
# ``json`` and ``long_text`` are omitted (not meaningfully filterable/groupable).
FILTERABLE_FIELD_TYPES = (
    "integer",
    "bigint",
    "numeric",
    "date",
    "timestamptz",
    "uuid",
    "picklist",
)

# Fail fast rather than pile up behind a busy table's locks.
_LOCK_TIMEOUT = "5s"

_PG_DIALECT = _pg.dialect()


def _sa_type(field_type: str) -> Any:
    """Map a catalog ``field_type`` to a SQLAlchemy column type instance."""
    match field_type:
        case "text" | "long_text" | "picklist":
            return Text()
        case "integer":
            return Integer()
        case "bigint":
            return BigInteger()
        case "numeric":
            return Numeric()
        case "boolean":
            return Boolean()
        case "date":
            return Date()
        case "timestamptz":
            return DateTime(timezone=True)
        case "uuid":
            return UUID(as_uuid=True)
        case "json":
            return JSONB()
        case _:
            raise ValueError(f"unsupported field_type: {field_type!r}")


class SchemaManager:
    """Executes custom-entity DDL on a privileged AsyncSession."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _set_lock_timeout(self) -> None:
        await self._session.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))

    # ------------------------------------------------------------------ #
    # RLS
    # ------------------------------------------------------------------ #
    async def _apply_rls(self, table_name: str) -> None:
        """ENABLE+FORCE RLS, (re)create the four tenant_isolation policies, and
        grant CRUD to app_user. Idempotent via DROP POLICY IF EXISTS."""
        qt = identifiers.quote(table_name)  # validates + quotes
        await self._session.execute(text(f"ALTER TABLE {qt} ENABLE ROW LEVEL SECURITY"))
        await self._session.execute(text(f"ALTER TABLE {qt} FORCE ROW LEVEL SECURITY"))
        for suffix, action, clause in _POLICIES:
            await self._session.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_{suffix} ON {qt}"))
            await self._session.execute(
                text(f"CREATE POLICY tenant_isolation_{suffix} ON {qt} FOR {action} {clause} ({_HARDENED})")
            )
        await self._session.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qt} TO app_user"))

    @staticmethod
    def _base_columns() -> list[Column]:
        # The org_id FK to orgs is added via ALTER after CreateTable: rendering a
        # Core ForeignKey would require `orgs` to live in this throwaway MetaData.
        return [
            Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
            Column("org_id", UUID(as_uuid=True), nullable=False),
            Column("created_at", DateTime(timezone=True), server_default=func.now()),
            Column("updated_at", DateTime(timezone=True), server_default=func.now()),
        ]

    def _field_column(self, field: EntityField) -> Column:
        col_name = identifiers.safe_identifier(field.physical_column)
        return Column(col_name, _sa_type(field.field_type), nullable=not field.is_required)

    def _type_sql(self, field_type: str) -> str:
        return _sa_type(field_type).compile(dialect=_PG_DIALECT)

    # ------------------------------------------------------------------ #
    # Entity tables
    # ------------------------------------------------------------------ #
    async def create_entity_table(self, definition: EntityDefinition, fields: list[EntityField]) -> None:
        """Create the physical table for a new entity (scalar fields only;
        relationships are added later via ``add_relationship``)."""
        await self._set_lock_timeout()
        table_name = identifiers.safe_identifier(definition.physical_table)

        md = MetaData()
        columns = self._base_columns()
        constraints: list[UniqueConstraint] = []

        for field in fields:
            columns.append(self._field_column(field))
            if field.is_unique:
                constraints.append(
                    UniqueConstraint(
                        "org_id",
                        field.physical_column,
                        name=identifiers.unique_constraint_name(field.id),
                    )
                )

        table = Table(table_name, md, *columns, *constraints)
        await self._session.execute(CreateTable(table, if_not_exists=True))
        # org_id FK to orgs (added post-create; see _base_columns).
        qt = identifiers.quote(table_name)
        org_fk = identifiers.quote(identifiers.fk_constraint_name(definition.id))
        await self._session.execute(
            text(
                f"ALTER TABLE {qt} ADD CONSTRAINT {org_fk} "
                "FOREIGN KEY (org_id) REFERENCES orgs(id) ON DELETE CASCADE"
            )
        )
        # Index org_id for tenant-scoped scans (mirrors the static schema).
        org_idx = Index(identifiers.index_name(definition.id), table.c["org_id"])
        await self._session.execute(CreateIndex(org_idx, if_not_exists=True))
        # Keyset-pagination index: the records grid pages by (created_at, id) DESC
        # without OFFSET, so this stays O(log n) on tables with millions of rows.
        await self._create_keyset_index(table_name, definition)
        # Trigram GIN indexes so case-insensitive substring search (ILIKE '%q%')
        # is index-backed rather than a full scan; plain btree indexes so
        # equality/range filters and GROUP BY on scalar fields stay index-backed.
        for field in fields:
            if field.field_type in SEARCHABLE_FIELD_TYPES:
                await self._create_trgm_index(table_name, field)
            if field.field_type in FILTERABLE_FIELD_TYPES:
                await self._create_btree_index(table_name, field)
        await self._apply_rls(table_name)

    async def _create_keyset_index(self, table_name: str, definition: EntityDefinition) -> None:
        qt = identifiers.quote(table_name)
        ix = identifiers.quote(identifiers.keyset_index_name(definition.id))
        await self._session.execute(
            text(f"CREATE INDEX IF NOT EXISTS {ix} ON {qt} (created_at DESC, id DESC)")
        )

    async def _create_trgm_index(self, table_name: str, field: EntityField) -> None:
        qt = identifiers.quote(table_name)
        qc = identifiers.quote(field.physical_column)
        ix = identifiers.quote(identifiers.trgm_index_name(field.id))
        await self._session.execute(
            text(f"CREATE INDEX IF NOT EXISTS {ix} ON {qt} USING gin ({qc} gin_trgm_ops)")
        )

    async def _create_btree_index(self, table_name: str, field: EntityField) -> None:
        # Composite (org_id, col DESC, id DESC): every tenant-scoped query filters
        # org_id first, so this serves `WHERE org_id = ? AND col <op> ?` and GROUP BY
        # col; the trailing `col DESC, id DESC` also matches the keyset ORDER BY the
        # record list emits for a custom descending sort (`ORDER BY col DESC, id
        # DESC`), so a filtered/sorted page is an ordered index scan, not a sort.
        qt = identifiers.quote(table_name)
        qc = identifiers.quote(field.physical_column)
        ix = identifiers.quote(identifiers.btree_index_name(field.id))
        await self._session.execute(
            text(f"CREATE INDEX IF NOT EXISTS {ix} ON {qt} (org_id, {qc} DESC, id DESC)")
        )

    async def drop_entity_table(self, definition: EntityDefinition) -> None:
        """Drop an entity's physical table (RLS policies drop with it)."""
        await self.drop_table_by_name(definition.physical_table)

    async def drop_table_by_name(self, table_name: str) -> None:
        """Drop a generated table (entity or M:M join) by its physical name."""
        await self._set_lock_timeout()
        name = identifiers.safe_identifier(table_name)
        table = Table(name, MetaData())
        await self._session.execute(DropTable(table, if_exists=True))

    async def add_field_column(self, definition: EntityDefinition, field: EntityField) -> None:
        await self._set_lock_timeout()
        qt = identifiers.quote(definition.physical_table)
        qc = identifiers.quote(field.physical_column)
        null_sql = " NOT NULL" if field.is_required else ""
        await self._session.execute(
            text(f"ALTER TABLE {qt} ADD COLUMN IF NOT EXISTS {qc} {self._type_sql(field.field_type)}{null_sql}")
        )
        if field.is_unique:
            uq = identifiers.quote(identifiers.unique_constraint_name(field.id))
            await self._session.execute(text(f"ALTER TABLE {qt} ADD CONSTRAINT {uq} UNIQUE (org_id, {qc})"))
        if field.field_type in SEARCHABLE_FIELD_TYPES:
            await self._create_trgm_index(definition.physical_table, field)
        if field.field_type in FILTERABLE_FIELD_TYPES:
            await self._create_btree_index(definition.physical_table, field)

    async def drop_field_column(self, definition: EntityDefinition, field: EntityField) -> None:
        await self._set_lock_timeout()
        qt = identifiers.quote(definition.physical_table)
        qc = identifiers.quote(field.physical_column)
        await self._session.execute(text(f"ALTER TABLE {qt} DROP COLUMN IF EXISTS {qc}"))

    # ------------------------------------------------------------------ #
    # Relationships
    # ------------------------------------------------------------------ #
    async def add_relationship(
        self,
        source: EntityDefinition,
        target: EntityDefinition,
        relationship: EntityRelationship,
    ) -> None:
        await self._set_lock_timeout()
        if relationship.on_delete not in _VALID_ON_DELETE:
            raise ValueError(f"invalid on_delete: {relationship.on_delete!r}")
        if relationship.cardinality == "many_to_many":
            await self._create_join_table(source, target, relationship)
        else:
            await self._add_fk_column(source, target, relationship)

    async def _add_fk_column(
        self,
        source: EntityDefinition,
        target: EntityDefinition,
        relationship: EntityRelationship,
    ) -> None:
        qt = identifiers.quote(source.physical_table)
        qc = identifiers.quote(relationship.physical_name)  # r_<hex>
        qtarget = identifiers.quote(target.physical_table)
        fk_name = identifiers.quote(identifiers.fk_constraint_name(relationship.id))
        idx_name = identifiers.quote(identifiers.index_name(relationship.id))
        # The FK column is always physically nullable — a required relationship is
        # enforced at the application layer (DynamicEntityRepository._to_row), so
        # adding it to an already-populated table never fails on existing NULLs.
        await self._session.execute(text(f"ALTER TABLE {qt} ADD COLUMN IF NOT EXISTS {qc} uuid"))
        await self._session.execute(
            text(
                f"ALTER TABLE {qt} ADD CONSTRAINT {fk_name} FOREIGN KEY ({qc}) "
                f"REFERENCES {qtarget}(id) ON DELETE {relationship.on_delete}"
            )
        )
        await self._session.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {qt} ({qc})"))
        if relationship.cardinality == "one_to_one":
            uq = identifiers.quote(identifiers.unique_constraint_name(relationship.id))
            await self._session.execute(text(f"ALTER TABLE {qt} ADD CONSTRAINT {uq} UNIQUE (org_id, {qc})"))

    async def _create_join_table(
        self,
        source: EntityDefinition,
        target: EntityDefinition,
        relationship: EntityRelationship,
    ) -> None:
        join_name = identifiers.safe_identifier(relationship.physical_name)  # cej_<hex>
        qj = identifiers.quote(join_name)
        qsource = identifiers.quote(source.physical_table)
        qtarget = identifiers.quote(target.physical_table)
        idx_name = identifiers.quote(identifiers.index_name(relationship.id))
        await self._session.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {qj} ("
                "  source_id uuid NOT NULL REFERENCES "
                f"{qsource}(id) ON DELETE CASCADE,"
                "  target_id uuid NOT NULL REFERENCES "
                f"{qtarget}(id) ON DELETE CASCADE,"
                "  org_id uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,"
                "  PRIMARY KEY (source_id, target_id)"
                ")"
            )
        )
        await self._session.execute(text(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {qj} (target_id)"))
        await self._apply_rls(join_name)
