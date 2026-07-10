"""Migration bundle format, id-remapping, and the import summary contract.

The bundle is a plain JSON document (see ``MigrationExporter``) so it stays
readable, diffable, and forward-compatible: resources are stored as dicts, not
tightly-versioned Pydantic rows. ``format_version`` guards the shape.
"""

from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# Bump ``BUNDLE_FORMAT_VERSION`` on any breaking change to the resource shapes.
BUNDLE_FORMAT_VERSION = 1
BUNDLE_KIND = "km2-migration-bundle"

# Resource sections, in dependency order (import must follow this order so that
# every reference target exists before the referrer is created).
RESOURCE_ORDER: tuple[str, ...] = (
    "tags",
    "entities",
    "connections",
    "folders",
    "workflows",
    "inbound_endpoints",
    "forms",
    "views",
    "records",
    "documents",
)

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# The field that identifies an item within its resource list, for selection
# filtering. Records are keyed by their entity slug (rows aren't individually
# selectable); everything else by ``id``.
_SELECTION_ID_FIELD: dict[str, str] = {"records": "entity_slug"}

# A selection maps a resource type to the ids (or record entity-slugs) to keep.
# Semantics: a type ABSENT from the selection keeps ALL of that type (so callers
# may omit types they don't filter); a type present with a list keeps only the
# listed ids; an empty list keeps none.
Selection = dict[str, list[str]]


def filter_resources(resources: dict[str, Any], selection: Selection | None) -> dict[str, Any]:
    """Return a copy of ``resources`` narrowed to the ``selection``.

    Used by both export (trim what leaves the source org) and import (trim what
    is applied to the target org)."""
    if not selection:
        return resources
    out: dict[str, Any] = {}
    for key, items in resources.items():
        if key not in selection or not isinstance(items, list):
            out[key] = items  # type not filtered → keep everything
            continue
        allowed = set(selection[key])
        id_field = _SELECTION_ID_FIELD.get(key, "id")
        out[key] = [it for it in items if str(it.get(id_field)) in allowed]
    return out


class CollisionStrategy(StrEnum):
    """What to do when an imported resource's natural key already exists in the
    target org."""

    SKIP = "skip"  # leave the existing resource; map references to it
    OVERWRITE = "overwrite"  # update the existing resource in place
    RENAME = "rename"  # create a suffixed copy alongside the existing one


# --------------------------------------------------------------------------- #
# Cross-reference remapping
# --------------------------------------------------------------------------- #
# JSON keys whose *value* is a UUID pointing at another exported resource. Used
# to rewrite ids embedded inside form/view ``config`` trees and workflow graph
# ``definition`` blobs so they point at the newly-created rows after import.
_REMAP_KEYS: dict[str, str] = {
    "relationship_id": "relationships",
    "anchor_relationship_id": "relationships",
    "workflow_id": "workflows",
    "answer_workflow_id": "workflows",
    "form_id": "forms",
    "entity_definition_id": "entities",
    "entity_id": "entities",
}


class IdMap:
    """Old-id -> new-id maps, one namespace per resource kind.

    ``record`` ids are namespaced per entity slug because record ids are only
    unique within a table.
    """

    def __init__(self) -> None:
        self._maps: dict[str, dict[str, str]] = {}

    def put(self, kind: str, old_id: Any, new_id: Any) -> None:
        self._maps.setdefault(kind, {})[str(old_id)] = str(new_id)

    def get(self, kind: str, old_id: Any) -> str | None:
        return self._maps.get(kind, {}).get(str(old_id))

    def namespace(self, kind: str) -> dict[str, str]:
        return self._maps.setdefault(kind, {})


def remap_refs(value: Any, id_map: IdMap, warnings: list[str], *, key: str | None = None) -> Any:
    """Deep-copy ``value`` (a JSON config/definition subtree), rewriting any UUID
    stored under a known cross-reference key to its post-import id.

    An unmapped UUID under a reference key is left unchanged and recorded as a
    warning — the reference will dangle, but the import does not fail.
    """
    if isinstance(value, dict):
        return {k: remap_refs(v, id_map, warnings, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [remap_refs(v, id_map, warnings, key=key) for v in value]
    if key in _REMAP_KEYS and isinstance(value, str) and _UUID_RE.match(value):
        mapped = id_map.get(_REMAP_KEYS[key], value)
        if mapped is not None:
            return mapped
        warnings.append(f"reference {key}={value} could not be remapped (target not in bundle)")
    return value


# --------------------------------------------------------------------------- #
# Import summary (API response)
# --------------------------------------------------------------------------- #
class ResourceOutcome(BaseModel):
    """Per-resource-kind tally of what the import did."""

    created: int = 0
    overwritten: int = 0
    renamed: int = 0
    skipped: int = 0
    failed: int = 0

    def record(self, action: str) -> None:
        setattr(self, action, getattr(self, action) + 1)


class GeneratedSecret(BaseModel):
    """A credential the import minted fresh (webhook endpoints) that the operator
    must copy to reconfigure external callers — shown once."""

    kind: str
    name: str
    token: str = ""
    url: str = ""
    signing_secret: str = ""
    signature_header: str = ""


class ImportSummary(BaseModel):
    """The full result of an import run."""

    strategy: CollisionStrategy
    dry_run: bool = False
    resources: dict[str, ResourceOutcome] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    generated_secrets: list[GeneratedSecret] = Field(default_factory=list)

    def outcome(self, kind: str) -> ResourceOutcome:
        return self.resources.setdefault(kind, ResourceOutcome())


def suffix_slug(slug: str, existing: set[str], *, max_len: int = 63, sep: str = "-") -> str:
    """Return ``slug`` with an ``imported``/``imported-2`` suffix that is not in
    ``existing`` (respecting the DB column length). ``sep`` is the word separator —
    use ``"_"`` for entity slugs (whose pattern forbids hyphens)."""
    base = slug[: max_len - len(f"{sep}imported{sep}99")]
    candidate = f"{base}{sep}imported"
    n = 2
    while candidate in existing:
        candidate = f"{base}{sep}imported{sep}{n}"
        n += 1
    return candidate


def suffix_name(name: str, existing: set[str], *, max_len: int = 200) -> str:
    """Return ``name`` with an ``(imported)`` suffix unique within ``existing``."""
    base = name[: max_len - len(" (imported 99)")]
    candidate = f"{base} (imported)"
    n = 2
    while candidate in existing:
        candidate = f"{base} (imported {n})"
        n += 1
    return candidate


def is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False
