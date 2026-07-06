"""Injection-safe identifier derivation for runtime custom-entity DDL.

Custom entities are backed by *real* physical Postgres tables created at
request time via DDL. Bind parameters cannot parameterize SQL identifiers, so
the only correct defense against identifier injection is to never let a
user-supplied string reach the DDL at all.

Every physical identifier is therefore **derived from a UUID** (whose canonical
hex form is ``[0-9a-f]`` only) behind a fixed prefix, and validated against a
strict allowlist regex before it is ever interpolated. The user-facing ``slug``
lives only in the catalog and is mapped to the physical name at query-build
time — which also makes renames zero-DDL.

Naming scheme (all lower-case, all <= 63 bytes, Postgres ``NAMEDATALEN``):

    ce_<hex32>    entity table            (from entity_definitions.id)
    cej_<hex32>   many-to-many join table (from entity_relationships.id)
    f_<hex32>     scalar column           (from entity_fields.id)
    r_<hex32>     relationship FK column  (from entity_relationships.id)
    fk_<hex32>    FK constraint           (from entity_relationships.id)
    uq_<hex32>    unique constraint       (from entity_fields.id / relationship.id)
    ix_<hex32>    index                   (from the owning object's id)
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import quoted_name

# Fixed prefixes for every kind of generated object.
TABLE_PREFIX = "ce_"
JOIN_TABLE_PREFIX = "cej_"
COLUMN_PREFIX = "f_"
RELATION_COLUMN_PREFIX = "r_"
FK_CONSTRAINT_PREFIX = "fk_"
UNIQUE_CONSTRAINT_PREFIX = "uq_"
INDEX_PREFIX = "ix_"

_ALL_PREFIXES = (
    TABLE_PREFIX,
    JOIN_TABLE_PREFIX,
    COLUMN_PREFIX,
    RELATION_COLUMN_PREFIX,
    FK_CONSTRAINT_PREFIX,
    UNIQUE_CONSTRAINT_PREFIX,
    INDEX_PREFIX,
)

# A generated identifier is a known prefix followed by exactly 32 hex chars.
_GENERATED_RE = re.compile(
    r"^(?:ce|cej|f|r|fk|uq|ix)_[0-9a-f]{32}$",
)

# Base columns present on every runtime entity table. These are the only
# non-generated identifiers ever allowed into DDL — a fixed, closed set.
BASE_COLUMNS = ("id", "org_id", "created_at", "updated_at")

# Postgres identifier byte limit (NAMEDATALEN - 1).
_MAX_IDENTIFIER_BYTES = 63

_preparer = postgresql.dialect().identifier_preparer


def _hex(value: uuid.UUID) -> str:
    """Return the 32-char lowercase hex form of a UUID."""
    if not isinstance(value, uuid.UUID):
        raise TypeError(f"expected uuid.UUID, got {type(value)!r}")
    return value.hex


def table_name(definition_id: uuid.UUID) -> str:
    """Physical table name for an entity definition."""
    return safe_identifier(f"{TABLE_PREFIX}{_hex(definition_id)}")


def join_table_name(relationship_id: uuid.UUID) -> str:
    """Physical join-table name for a many-to-many relationship."""
    return safe_identifier(f"{JOIN_TABLE_PREFIX}{_hex(relationship_id)}")


def column_name(field_id: uuid.UUID) -> str:
    """Physical column name for a scalar field."""
    return safe_identifier(f"{COLUMN_PREFIX}{_hex(field_id)}")


def relation_column_name(relationship_id: uuid.UUID) -> str:
    """Physical FK column name for a to-one relationship."""
    return safe_identifier(f"{RELATION_COLUMN_PREFIX}{_hex(relationship_id)}")


def fk_constraint_name(relationship_id: uuid.UUID) -> str:
    """FK constraint name for a relationship."""
    return safe_identifier(f"{FK_CONSTRAINT_PREFIX}{_hex(relationship_id)}")


def unique_constraint_name(object_id: uuid.UUID) -> str:
    """Unique constraint name derived from the owning object's id."""
    return safe_identifier(f"{UNIQUE_CONSTRAINT_PREFIX}{_hex(object_id)}")


def index_name(object_id: uuid.UUID) -> str:
    """Index name derived from the owning object's id."""
    return safe_identifier(f"{INDEX_PREFIX}{_hex(object_id)}")


# Fixed namespaces so a single object can own several distinct indexes without
# name collisions. uuid5 is deterministic, so the derived index names are stable
# across processes (required for ``IF NOT EXISTS`` idempotency).
_KEYSET_INDEX_NS = uuid.UUID("b6c2e3a4-1f5d-4e7c-9a0b-2d3f4e5a6b7c")
_TRGM_INDEX_NS = uuid.UUID("c7d3f4b5-2a6e-4f8d-8b1c-3e4a5b6c7d8e")


def keyset_index_name(definition_id: uuid.UUID) -> str:
    """Index name for the ``(created_at, id)`` keyset-pagination index."""
    return index_name(uuid.uuid5(_KEYSET_INDEX_NS, definition_id.hex))


def trgm_index_name(field_id: uuid.UUID) -> str:
    """Index name for a per-column trigram (substring-search) GIN index."""
    return index_name(uuid.uuid5(_TRGM_INDEX_NS, field_id.hex))


def is_generated_identifier(name: str) -> bool:
    """True if ``name`` is one of our prefix+hex generated identifiers."""
    return isinstance(name, str) and _GENERATED_RE.fullmatch(name) is not None


def safe_identifier(name: str) -> str:
    """Validate ``name`` as a safe SQL identifier or raise ``ValueError``.

    Accepts **only** a generated ``<prefix>_<hex32>`` name or one of the fixed
    ``BASE_COLUMNS``. Every other string — including any user-supplied value and
    any malformed generated name — is rejected. This is the single chokepoint
    every identifier must pass before being interpolated into DDL, so the
    accepted set is deliberately closed rather than "anything snake_case".
    """
    if not isinstance(name, str):
        raise ValueError(f"identifier must be str, got {type(name)!r}")
    if len(name.encode("utf-8")) > _MAX_IDENTIFIER_BYTES:
        raise ValueError(f"identifier exceeds {_MAX_IDENTIFIER_BYTES} bytes: {name!r}")
    if not is_generated_identifier(name) and name not in BASE_COLUMNS:
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def quote(name: str) -> quoted_name:
    """Return a validated, dialect-quoted identifier safe to inline into DDL."""
    return _preparer.quote(safe_identifier(name))
