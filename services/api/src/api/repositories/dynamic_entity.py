"""Record CRUD for custom-entity physical tables.

Runtime entity tables have no static ORM class, so records are read/written via
SQLAlchemy Core ``Table`` objects built from the catalog. This repository runs
under ``get_tenant_db`` (``app_user`` + RLS): tenant isolation is enforced by
the database, and every query *also* filters ``org_id`` explicitly.

Public payloads are keyed by field **slug**; the repository translates slugs to
physical column names (``f_<hex>`` / ``r_<hex>``) so a raw client-supplied
column name can never reach SQL. Filter/sort keys are whitelisted to catalog
slugs for the same reason.
"""

from __future__ import annotations

import operator
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple

from sqlalchemy import Column, MetaData, Table, and_, bindparam, distinct, false, func, or_, select, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.custom_entity import EntityDefinition, EntityField, EntityRelationship
from api.models.workflow import WorkflowOutbox
from api.repositories.workflow import OutboxWriter
from api.schemas.aggregate import AggregateQuery, AggregateResult
from api.services import identifiers
from api.services.schema_manager import SEARCHABLE_FIELD_TYPES, _sa_type

# Aggregate operators whose numeric result requires a numeric column.
_NUMERIC_AGG_OPS = ("sum", "avg")
# Field types over which min/max is meaningful (numeric or chronological).
_ORDERABLE_FIELD_TYPES = ("integer", "bigint", "numeric", "date", "timestamptz")
_NUMERIC_FIELD_TYPES = ("integer", "bigint", "numeric")

# Case-insensitive string spellings accepted for a JSON-delivered boolean field.
_TRUE_STRINGS = frozenset({"true", "t", "1", "yes", "y", "on"})
_FALSE_STRINGS = frozenset({"false", "f", "0", "no", "n", "off"})

# Base columns present on every entity table, exposed read-only.
_BASE_READ_COLUMNS = ("id", "created_at", "updated_at")

class RecordCursor(NamedTuple):
    """Keyset position: the sort key + id of the last row on the previous page.

    Carries the sort ``order_slug``/``order_dir`` so the endpoint can reject a
    cursor reused under a different sort. ``order_value`` is the raw (JSON string
    or scalar) form the endpoint serialises; the repository re-coerces it to the
    column's Python type before comparing.
    """

    order_slug: str
    order_dir: str
    order_value: Any
    id: uuid.UUID


# Filter operators accepted by ``list()``. Mapped to SQL comparisons in
# ``_filter_condition``; the router validates the raw op string before it here.
FILTER_OPERATORS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "isnull"})

# A filter clause is (field-slug, operator, value).
FilterClause = tuple[str, str, Any]

# Scalar comparison operators, shared by filter ``gt/gte/...`` clauses and the
# aggregation engine's HAVING clauses. Each takes ``(column_expr, value)``.
_COMPARATORS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
}

# Guard against pathologically large record payloads (an entity is capped at
# ~100 fields; this is a generous ceiling well above that).
_MAX_PAYLOAD_KEYS = 500


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so user input is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class EntityRecordError(ValueError):
    """Raised for invalid record payloads (mapped to HTTP 400 by the router)."""


class DynamicEntityRepository:
    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        definition: EntityDefinition,
        fields: list[EntityField],
        relationships: list[EntityRelationship] | None = None,
        *,
        outbox: OutboxWriter | None = None,
        actor_user_id: uuid.UUID | None = None,
        origin_run_id: uuid.UUID | None = None,
        outbox_source: str = "record",
    ) -> None:
        self._session = session
        self._org_id = org_id
        self._definition = definition
        self._fields = fields
        # Only to-one relationships add a physical FK column on this table.
        self._relationships = [r for r in (relationships or []) if r.cardinality != "many_to_many"]
        self._table = self._build_table()
        # Optional transactional change capture for the workflow engine.
        self._outbox = outbox
        self._actor_user_id = actor_user_id
        self._origin_run_id = origin_run_id
        self._outbox_source = outbox_source
        # The outbox row written by the most recent create/update/delete on this
        # repo instance (one repo == one request). Lets an inline dispatcher key
        # off THIS write's exact event instead of re-querying (which races a
        # concurrent writer on the same record). ``None`` until the first write.
        self._last_change_event: WorkflowOutbox | None = None

        # slug <-> physical maps for writable columns (fields + to-one FKs).
        self._slug_to_col: dict[str, str] = {f.slug: f.physical_column for f in fields}
        self._slug_to_col.update({r.slug: r.physical_name for r in self._relationships})
        self._col_to_slug = {v: k for k, v in self._slug_to_col.items()}
        self._field_by_slug = {f.slug: f for f in fields}
        self._rel_by_slug = {r.slug: r for r in self._relationships}

    @property
    def last_change_event(self) -> WorkflowOutbox | None:
        """The ``workflow_outbox`` row from this repo's most recent write (or None)."""
        return self._last_change_event

    def _build_table(self) -> Table:
        md = MetaData()
        cols: list[Column] = [
            Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
            Column("org_id", UUID(as_uuid=True), nullable=False),
            Column("created_at", _sa_type("timestamptz")),
            Column("updated_at", _sa_type("timestamptz")),
        ]
        for f in self._fields:
            cols.append(Column(identifiers.safe_identifier(f.physical_column), _sa_type(f.field_type)))
        for r in self._relationships:
            cols.append(Column(identifiers.safe_identifier(r.physical_name), UUID(as_uuid=True)))
        return Table(identifiers.safe_identifier(self._definition.physical_table), md, *cols)

    # ------------------------------------------------------------------ #
    # Payload translation / validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _coerce_value(field: EntityField | None, value: Any) -> Any:
        """Coerce a JSON-decoded scalar into the Python type asyncpg requires.

        JSON only carries strings/numbers/booleans, but asyncpg's binary codecs
        are strict: a ``date`` column needs a ``datetime.date``; a numeric column
        rejects the string ``"abc"``; an integer column rejects ``"42"`` /
        ``"true"``. Handing any of those to the codec raises deep in the driver
        and surfaces as an unhandled HTTP 500. We coerce + validate every scalar
        type whose JSON form can mismatch here; malformed input raises
        ``EntityRecordError`` (mapped to HTTP 400) instead.

        Only *string* inputs are coerced — a JSON number/boolean already decodes
        to the right Python type and passes through untouched.
        """
        if field is None or not isinstance(value, str):
            return value
        if field.field_type == "date":
            try:
                return datetime.fromisoformat(value).date() if "T" in value else date.fromisoformat(value)
            except ValueError as exc:
                raise EntityRecordError(f"{field.slug!r} must be a valid date (YYYY-MM-DD)") from exc
        if field.field_type == "timestamptz":
            try:
                dt = datetime.fromisoformat(value)
            except ValueError as exc:
                raise EntityRecordError(f"{field.slug!r} must be a valid date-time") from exc
            # A ``datetime-local`` input carries no zone; treat naive values as UTC
            # so asyncpg (which rejects naive datetimes for timestamptz) accepts it.
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        if field.field_type in ("integer", "bigint"):
            # int() is strict: it rejects trailing garbage ("42abc"), floats
            # ("42.5"), and non-numeric text ("true"/"abc").
            try:
                return int(value.strip())
            except ValueError as exc:
                raise EntityRecordError(f"{field.slug!r} must be a whole number") from exc
        if field.field_type == "numeric":
            try:
                dec = Decimal(value.strip())
            except (InvalidOperation, ValueError) as exc:
                raise EntityRecordError(f"{field.slug!r} must be a number") from exc
            if not dec.is_finite():  # reject NaN / Infinity that Decimal accepts but is nonsense here
                raise EntityRecordError(f"{field.slug!r} must be a finite number")
            return dec
        if field.field_type == "boolean":
            normalized = value.strip().casefold()
            if normalized in _TRUE_STRINGS:
                return True
            if normalized in _FALSE_STRINGS:
                return False
            raise EntityRecordError(f"{field.slug!r} must be a boolean")
        return value

    def _to_row(self, payload: dict[str, Any], *, for_create: bool) -> dict[str, Any]:
        """Translate a slug-keyed payload to a physical-column-keyed row,
        validating unknown keys, picklist membership, and required presence."""
        if len(payload) > _MAX_PAYLOAD_KEYS:
            raise EntityRecordError(f"too many fields (max {_MAX_PAYLOAD_KEYS})")
        unknown = set(payload) - set(self._slug_to_col)
        if unknown:
            raise EntityRecordError(f"unknown fields: {sorted(unknown)}")

        row: dict[str, Any] = {}
        for slug, value in payload.items():
            field = self._field_by_slug.get(slug)
            rel = self._rel_by_slug.get(slug)
            # Explicitly setting a required scalar/relationship to null is a 400,
            # not a 500. Without this, a required scalar hits the column's NOT NULL
            # constraint (unhandled IntegrityError) and a required relationship —
            # whose FK column is physically nullable — would silently null out.
            if value is None and ((field is not None and field.is_required) or (rel is not None and rel.is_required)):
                raise EntityRecordError(f"{slug!r} is required and cannot be null")
            if field is not None and field.field_type == "picklist" and value is not None:
                options = field.picklist_options or []
                if value not in options:
                    raise EntityRecordError(f"{slug!r} must be one of {options}")
            row[self._slug_to_col[slug]] = self._coerce_value(field, value)

        if for_create:
            for f in self._fields:
                if f.is_required and payload.get(f.slug) is None:
                    raise EntityRecordError(f"{f.slug!r} is required")
            for r in self._relationships:
                if r.is_required and payload.get(r.slug) is None:
                    raise EntityRecordError(f"{r.slug!r} is required")
        return row

    def _to_public(self, row: Any) -> dict[str, Any]:
        """Translate a physical DB row (mapping) to a slug-keyed dict."""
        m = row._mapping if hasattr(row, "_mapping") else row
        out: dict[str, Any] = {c: m[c] for c in _BASE_READ_COLUMNS if c in m}
        for col, slug in self._col_to_slug.items():
            if col in m:
                out[slug] = m[col]
        return out

    def _column(self, slug: str) -> Column:
        # Base columns (id/created_at/updated_at) are fixed, injection-safe
        # identifiers always present on the table — allow sort/filter by them.
        if slug in _BASE_READ_COLUMNS:
            return self._table.c[slug]
        col_name = self._slug_to_col.get(slug)
        if col_name is None:
            raise EntityRecordError(f"unknown field: {slug!r}")
        return self._table.c[col_name]

    def _coerce_by_slug(self, slug: str, value: Any) -> Any:
        """Coerce a filter / cursor value (typically a JSON string) to the Python
        type the column's asyncpg codec requires.

        Reuses ``_coerce_value`` for catalog scalar fields (which raises a clean
        ``EntityRecordError`` on a mistyped value). Relationship FK slugs and the
        base ``id`` column coerce to ``uuid``; ``created_at`` / ``updated_at``
        coerce to a timezone-aware ``datetime`` — so filtering/sorting a
        date-range or a related-record id never hands a bare string to the driver.
        """
        field = self._field_by_slug.get(slug)
        if field is not None:
            return self._coerce_value(field, value)
        if value is None:
            return None
        if slug in self._rel_by_slug or slug == "id":
            if isinstance(value, uuid.UUID):
                return value
            try:
                return uuid.UUID(str(value))
            except (ValueError, AttributeError) as exc:
                raise EntityRecordError(f"{slug!r} must be a valid record id") from exc
        if slug in ("created_at", "updated_at") and isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value)
            except ValueError as exc:
                raise EntityRecordError(f"{slug!r} must be a valid date-time") from exc
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _normalize_filters(filters: dict[str, Any] | list[FilterClause] | None) -> list[FilterClause]:
        """Accept either a legacy ``{slug: value}`` dict (implicit ``eq``) or a
        list of explicit ``(slug, op, value)`` clauses."""
        if not filters:
            return []
        if isinstance(filters, dict):
            return [(slug, "eq", value) for slug, value in filters.items()]
        return list(filters)

    def _filter_condition(self, slug: str, op: str, value: Any) -> Any:
        """Build one SQL predicate for a filter clause, whitelisting the slug and
        coercing the comparison value to the column type."""
        col = self._column(slug)  # validates slug (raises on unknown)
        if op == "isnull":
            return col.is_(None) if bool(value) else col.isnot(None)
        if op == "in":
            values = value if isinstance(value, (list, tuple)) else [value]
            if not values:
                return false()  # `IN ()` matches nothing
            return col.in_([self._coerce_by_slug(slug, v) for v in values])
        if op == "contains":
            field = self._field_by_slug.get(slug)
            if field is None or field.field_type not in SEARCHABLE_FIELD_TYPES:
                raise EntityRecordError(f"{slug!r} does not support the 'contains' filter")
            return col.ilike(f"%{_escape_like(str(value))}%", escape="\\")
        comparator = _COMPARATORS.get(op)
        if comparator is None:
            raise EntityRecordError(f"unknown filter operator: {op!r}")
        return comparator(col, self._coerce_by_slug(slug, value))

    def _keyset_after(self, order_col: Column, order_value: Any, rec_id_value: uuid.UUID, *, descending: bool) -> Any:
        """Predicate selecting rows strictly *after* ``(order_value, rec_id_value)``
        under ``ORDER BY order_col <dir> NULLS LAST, id DESC``.

        Handles NULL sort keys explicitly (NULLS LAST): a non-null cursor page
        continues into any NULL-keyed rows; once the cursor key is NULL only
        smaller-id NULL rows remain. The ``id`` tiebreaker is always DESC.
        """
        id_col = self._table.c.id
        id_tie = id_col < rec_id_value
        if order_value is None:
            return and_(order_col.is_(None), id_tie)
        beyond = order_col < order_value if descending else order_col > order_value
        return or_(beyond, order_col.is_(None), and_(order_col == order_value, id_tie))

    def _field_type(self, slug: str) -> str | None:
        """Catalog ``field_type`` for a slug, or the effective type of a base /
        relationship column (used to validate aggregate ops and date buckets)."""
        field = self._field_by_slug.get(slug)
        if field is not None:
            return field.field_type
        if slug in ("created_at", "updated_at"):
            return "timestamptz"
        if slug == "id" or slug in self._rel_by_slug:
            return "uuid"
        return None

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    async def _capture(
        self, operation: str, record_id: uuid.UUID, before: dict[str, Any] | None, after: dict[str, Any] | None
    ) -> None:
        if self._outbox is None:
            return
        self._last_change_event = await self._outbox.write(
            org_id=self._org_id,
            entity_definition_id=self._definition.id,
            entity_table=self._definition.physical_table,
            operation=operation,
            record_id=record_id,
            before=before,
            after=after,
            actor_user_id=self._actor_user_id,
            origin_run_id=self._origin_run_id,
            source=self._outbox_source,
        )

    async def _validate_relationships(self, payload: dict[str, Any]) -> None:
        """Reject relationship values that point at a record outside this org.

        Postgres FK checks run as the table owner and bypass RLS, so the DB alone
        won't stop a cross-tenant link — validate ownership explicitly against the
        (trusted, catalog-derived) target table filtered by org_id.

        Batched to avoid an N+1: one query resolves every target's physical table
        and one existence query runs per *distinct* target table (relationships
        are capped at 50, and only present, non-null ones are checked).
        """
        present: list[tuple[str, EntityRelationship, uuid.UUID]] = []
        for slug, rel in self._rel_by_slug.items():
            if slug not in payload or payload[slug] is None:
                continue
            try:
                value = uuid.UUID(str(payload[slug]))
            except (ValueError, TypeError) as exc:
                raise EntityRecordError(f"{slug!r} must be a record id (uuid)") from exc
            present.append((slug, rel, value))
        if not present:
            return

        # One query for all target physical tables (org-scoped).
        target_def_ids = {rel.target_definition_id for _, rel, _ in present}
        rows = (
            await self._session.execute(
                select(EntityDefinition.id, EntityDefinition.physical_table).where(
                    EntityDefinition.id.in_(target_def_ids),
                    EntityDefinition.org_id == self._org_id,
                )
            )
        ).all()
        table_by_def = {r[0]: r[1] for r in rows}

        # Group the ids to verify by physical table so each distinct table costs
        # a single existence query rather than one per relationship.
        ids_by_table: dict[str, set[uuid.UUID]] = {}
        for slug, rel, value in present:
            table = table_by_def.get(rel.target_definition_id)
            if table is None:
                raise EntityRecordError(f"unknown relationship target for {slug!r}")
            ids_by_table.setdefault(table, set()).add(value)

        for table, ids in ids_by_table.items():
            # table is a catalog-derived generated identifier validated + quoted
            # by identifiers.quote(); ids/org are bound parameters (expanding IN).
            sql = f"SELECT id FROM {identifiers.quote(table)} WHERE org_id = :org AND id IN :ids"  # noqa: S608
            stmt = text(sql).bindparams(bindparam("ids", expanding=True))
            found = set((await self._session.execute(stmt, {"org": self._org_id, "ids": list(ids)})).scalars().all())
            missing = ids - found
            if missing:
                bad_slug = next(
                    slug
                    for slug, rel, value in present
                    if table_by_def.get(rel.target_definition_id) == table and value in missing
                )
                raise EntityRecordError(f"{bad_slug!r} references a record not in this org")

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self._validate_relationships(payload)
        row = self._to_row(payload, for_create=True)
        row["org_id"] = self._org_id
        stmt = self._table.insert().values(**row).returning(*self._table.c)
        result = await self._session.execute(stmt)
        created = self._to_public(result.one())
        await self._capture("create", uuid.UUID(str(created["id"])), None, created)
        return created

    async def get(self, record_id: uuid.UUID) -> dict[str, Any] | None:
        stmt = select(*self._table.c).where(
            self._table.c.id == record_id,
            self._table.c.org_id == self._org_id,
        )
        result = await self._session.execute(stmt)
        found = result.one_or_none()
        return self._to_public(found) if found is not None else None

    async def list(
        self,
        *,
        filters: dict[str, Any] | list[FilterClause] | None = None,
        search: str | None = None,
        cursor: RecordCursor | None = None,
        limit: int = 50,
        order_by: str | None = None,
        order_dir: str = "desc",
    ) -> tuple[list[dict[str, Any]], RecordCursor | None]:
        """Keyset-paginated, filtered record page.

        Returns ``(items, next_cursor)`` where ``next_cursor`` is ``None`` on the
        last page. Uses no ``OFFSET`` and no ``COUNT`` so it stays fast on tables
        with millions of rows.

        ``filters`` is either a legacy ``{slug: value}`` dict (implicit ``eq``) or
        a list of ``(slug, op, value)`` clauses (``eq/ne/gt/gte/lt/lte/in/
        contains/isnull``); values are coerced with the same rules as writes so a
        mistyped filter raises a clean ``EntityRecordError`` (HTTP 400).
        ``search`` is a case-insensitive substring match across text columns
        (trigram-index-backed).

        ``order_by`` (a field slug or base column) + ``order_dir`` override the
        sort. Keyset ``cursor`` pagination works under *any* sort and with filters
        applied — the cursor carries the sort key so a page continues correctly;
        a cursor reused under a different sort raises ``EntityRecordError``.
        """
        limit = max(1, min(limit, 200))
        order_slug = order_by or "created_at"
        order_col = self._column(order_slug)  # validates the slug (raises on unknown)
        descending = str(order_dir).lower() != "asc"
        rec_id = self._table.c.id

        conditions = [self._table.c.org_id == self._org_id]
        for slug, op, value in self._normalize_filters(filters):
            conditions.append(self._filter_condition(slug, op, value))

        if search and search.strip():
            pattern = f"%{_escape_like(search.strip())}%"
            searchable = [
                self._table.c[f.physical_column] for f in self._fields if f.field_type in SEARCHABLE_FIELD_TYPES
            ]
            if not searchable:
                return [], None  # nothing to match a text query against
            conditions.append(or_(*[col.ilike(pattern, escape="\\") for col in searchable]))

        if cursor is not None:
            if cursor.order_slug != order_slug or (cursor.order_dir == "desc") != descending:
                raise EntityRecordError("cursor does not match the requested sort order")
            cursor_value = self._coerce_by_slug(order_slug, cursor.order_value)
            conditions.append(self._keyset_after(order_col, cursor_value, cursor.id, descending=descending))

        # NULLS LAST keeps the keyset predicate's null handling correct; id is a
        # stable, unique tiebreaker so equal sort keys order deterministically.
        # (The tiebreaker stays id DESC to match _keyset_after's cursor predicate.)
        primary = (order_col.desc() if descending else order_col.asc()).nullslast()
        stmt = (
            select(*self._table.c)
            .where(*conditions)
            .order_by(primary, rec_id.desc())
            .limit(limit + 1)  # one extra row tells us whether another page exists
        )
        rows = (await self._session.execute(stmt)).all()

        has_more = len(rows) > limit
        page = rows[:limit]
        items = [self._to_public(r) for r in page]
        next_cursor: RecordCursor | None = None
        if has_more and page:
            last = page[-1]._mapping
            next_cursor = RecordCursor(
                order_slug=order_slug,
                order_dir="desc" if descending else "asc",
                order_value=last[order_col.name],
                id=last["id"],
            )
        return items, next_cursor

    # ------------------------------------------------------------------ #
    # Aggregation (reporting engine)
    # ------------------------------------------------------------------ #
    def _aggregate_expr(self, op: str, field: str | None) -> Any:
        """Build one aggregate SQL expression, validating the op/field-type pair.

        ``count`` needs no field; ``sum``/``avg`` require a numeric field;
        ``min``/``max`` require a numeric or chronological field; ``count_distinct``
        works over any column. The field slug is whitelisted by ``_column``.
        """
        if op == "count":
            return func.count()
        if field is None:
            raise EntityRecordError(f"aggregate {op!r} requires a field")
        col = self._column(field)  # validates the slug
        if op == "count_distinct":
            return func.count(distinct(col))
        ftype = self._field_type(field)
        if op in _NUMERIC_AGG_OPS:
            if ftype not in _NUMERIC_FIELD_TYPES:
                raise EntityRecordError(f"{op!r} requires a numeric field, got {field!r} ({ftype})")
            return func.sum(col) if op == "sum" else func.avg(col)
        if op in ("min", "max"):
            if ftype not in _ORDERABLE_FIELD_TYPES:
                raise EntityRecordError(f"{op!r} requires a numeric or date field, got {field!r} ({ftype})")
            return func.min(col) if op == "min" else func.max(col)
        raise EntityRecordError(f"unknown aggregate op: {op!r}")

    def _build_aggregate(self, query: AggregateQuery) -> tuple[Any, list[str], list[str]]:
        """Build the aggregate SQL statement (no execution) and the ordered group
        and metric column names. Split out from :meth:`aggregate` so the query
        construction (and its validation) is unit-testable without a database."""
        used_names: set[str] = set()
        order_exprs: dict[str, Any] = {}  # result name -> expression (group or metric)
        having_exprs: dict[str, Any] = {}  # metric name -> aggregate expression

        def _claim(name: str) -> str:
            if name in used_names:
                raise EntityRecordError(f"duplicate result column {name!r}")
            used_names.add(name)
            return name

        group_labels: list[str] = []
        group_exprs: list[Any] = []
        selected: list[Any] = []
        for g in query.group_by:
            col = self._column(g.field)  # validates the slug
            if g.bucket is not None:
                if self._field_type(g.field) not in ("date", "timestamptz"):
                    raise EntityRecordError(f"{g.field!r} is not a date field; cannot bucket by {g.bucket!r}")
                expr = func.date_trunc(g.bucket, col)
                default = f"{g.field}_{g.bucket}"
            else:
                expr = col
                default = g.field
            name = _claim(g.alias or default)
            group_labels.append(name)
            group_exprs.append(expr)
            selected.append(expr.label(name))
            order_exprs[name] = expr

        metric_labels: list[str] = []
        for m in query.metrics:
            expr = self._aggregate_expr(m.op, m.field)
            default = m.op if m.op == "count" else f"{m.op}_{m.field}"
            name = _claim(m.alias or default)
            metric_labels.append(name)
            selected.append(expr.label(name))
            order_exprs[name] = expr
            having_exprs[name] = expr

        conditions = [self._table.c.org_id == self._org_id]
        for f in query.filters:
            conditions.append(self._filter_condition(f.field, f.op, f.value))

        stmt = select(*selected).where(*conditions)
        if group_exprs:
            stmt = stmt.group_by(*group_exprs)

        for h in query.having:
            expr = having_exprs.get(h.metric)
            if expr is None:
                raise EntityRecordError(f"having references unknown metric {h.metric!r}")
            stmt = stmt.having(_COMPARATORS[h.op](expr, h.value))

        for o in query.order_by:
            expr = order_exprs.get(o.key)
            if expr is None:
                raise EntityRecordError(f"order_by references unknown column {o.key!r}")
            stmt = stmt.order_by(expr.desc() if o.dir == "desc" else expr.asc())

        stmt = stmt.limit(query.limit)
        return stmt, group_labels, metric_labels

    async def aggregate(self, query: AggregateQuery) -> AggregateResult:
        """Run a GROUP BY / metric query for the reporting engine.

        Returns rows keyed by group-key and metric name. Every field reference is
        whitelisted to a physical column and every op/bucket comes from a closed
        Literal set, so no user string reaches SQL as an identifier; the query
        runs under the tenant's RLS session with an explicit ``org_id`` filter.
        """
        stmt, group_labels, metric_labels = self._build_aggregate(query)
        rows = (await self._session.execute(stmt)).mappings().all()
        return AggregateResult(
            group_by=group_labels,
            metrics=metric_labels,
            rows=[dict(r) for r in rows],
            row_count=len(rows),
        )

    async def update(self, record_id: uuid.UUID, patch: dict[str, Any]) -> dict[str, Any] | None:
        await self._validate_relationships(patch)
        row = self._to_row(patch, for_create=False)
        if not row:
            return await self.get(record_id)
        # NOTE: the "before" audit snapshot is read without a FOR UPDATE row lock,
        # so a concurrent update between this read and the UPDATE below could make
        # the captured "before" slightly stale. Acceptable for change-capture
        # (the outbox is advisory, not the source of truth); revisit with a
        # locked read if exact before/after diffs become a hard requirement.
        before = await self.get(record_id) if self._outbox is not None else None
        row["updated_at"] = func.now()
        stmt = (
            self._table.update()
            .where(self._table.c.id == record_id, self._table.c.org_id == self._org_id)
            .values(**row)
            .returning(*self._table.c)
        )
        result = await self._session.execute(stmt)
        found = result.one_or_none()
        if found is None:
            return None
        after = self._to_public(found)
        await self._capture("update", record_id, before, after)
        return after

    async def delete(self, record_id: uuid.UUID) -> bool:
        before = await self.get(record_id) if self._outbox is not None else None
        stmt = (
            self._table.delete()
            .where(self._table.c.id == record_id, self._table.c.org_id == self._org_id)
            .returning(self._table.c.id)
        )
        result = await self._session.execute(stmt)
        deleted = result.one_or_none() is not None
        if deleted:
            await self._capture("delete", record_id, before, None)
        return deleted
