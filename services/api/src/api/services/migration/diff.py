"""Object-level diff between a source bundle and a target org's current state.

Given two bundles (a source export and the target org's own current-state export,
both produced by :class:`MigrationExporter`), classify every config object as
``added`` / ``changed`` / ``unchanged`` / ``deleted``. This powers the
change-management *preview*: an admin sees exactly what a promotion would do
before applying it.

Two identity-independent mechanisms make the diff meaningful across environments:

* **Correlation** is by durable ``lineage_id`` first, falling back to the natural
  key (slug/name) on the first promotion (before the target has adopted the
  lineage). So a renamed object still correlates once linked, and a
  never-yet-promoted object still correlates by slug.
* **Change detection** is a content ``fingerprint`` — a hash of the exported dict
  with identity/volatile keys stripped and embedded cross-references normalized to
  *lineage* space, so a pure id-remap between environments does not read as a
  change.

Records and documents are DATA, not config: they are reported count-only by
default (a per-row content diff over tens of thousands of rows is rarely what a
config promotion cares about) and are never flagged for deletion here.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from api.services.migration.bundle import _REMAP_KEYS, _UUID_RE, RESOURCE_ORDER

# Data-layer resource types: diffed by count only, never correlated per row.
DATA_RESOURCE_TYPES: frozenset[str] = frozenset({"records", "documents"})

# The natural key per resource type, used to correlate when lineage does not match
# (the first promotion, before the target row has adopted the source's lineage).
_NATURAL_KEY: dict[str, str] = {
    "tags": "name",
    "entities": "slug",
    "connections": "name",
    "folders": "dot_path",
    "workflows": "name",
    "inbound_endpoints": "name",
    "reports": "slug",
    "forms": "slug",
    "views": "slug",
    "mcp_servers": "name",
    "agents": "name",
    "documents": "title",
}

# Keys stripped everywhere (recursively) before hashing: identity + server-managed
# timestamps, all of which legitimately differ across environments.
_VOLATILE_KEYS: frozenset[str] = frozenset({"id", "lineage_id", "created_at", "updated_at"})


class ObjectStatus(StrEnum):
    ADDED = "added"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    DELETED = "deleted"


class ObjectDiff(BaseModel):
    """One object's place in the diff (unchanged objects are counted, not listed)."""

    resource_type: str
    status: ObjectStatus
    lineage_id: str | None = None
    natural_key: str = ""
    name: str | None = None


class ResourceDiff(BaseModel):
    """Per-resource-type tally plus the added/changed/deleted objects."""

    resource_type: str
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    count_only: bool = False  # data layer: counts are informational, not correlated
    objects: list[ObjectDiff] = Field(default_factory=list)


class BundleDiff(BaseModel):
    """The full diff, grouped by resource type in dependency order."""

    resources: list[ResourceDiff] = Field(default_factory=list)
    # Resource types with deletes, in REVERSE dependency order (children first) —
    # the order a promotion must apply deletes in.
    delete_order: list[str] = Field(default_factory=list)
    has_deletes: bool = False
    totals: dict[str, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Correlation
# --------------------------------------------------------------------------- #
def _as_list(value: Any) -> list[dict]:
    return value if isinstance(value, list) else []


def _natural_key(resource_type: str, obj: dict) -> str:
    return str(obj.get(_NATURAL_KEY.get(resource_type, "name"), ""))


def _name(obj: dict) -> str | None:
    return obj.get("name") or obj.get("title") or obj.get("slug")


def _index(resource_type: str, objs: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (by_lineage, by_natural) lookups over the target objects."""
    by_lineage: dict[str, dict] = {}
    by_natural: dict[str, dict] = {}
    for obj in objs:
        lin = obj.get("lineage_id")
        if lin:
            by_lineage[str(lin)] = obj
        by_natural.setdefault(_natural_key(resource_type, obj), obj)
    return by_lineage, by_natural


def _match_target(
    resource_type: str, src: dict, by_lineage: dict[str, dict], by_natural: dict[str, dict]
) -> dict | None:
    """Lineage match first (rename-proof once linked), else natural key."""
    lin = src.get("lineage_id")
    if lin and str(lin) in by_lineage:
        return by_lineage[str(lin)]
    return by_natural.get(_natural_key(resource_type, src))


# --------------------------------------------------------------------------- #
# Content fingerprint
# --------------------------------------------------------------------------- #
def _lineage_by_id(resources: dict[str, Any]) -> dict[str, str]:
    """Map every object's local ``id`` -> its ``lineage_id`` (recursively, so
    nested entity fields/relationships are included) for ref normalization."""
    out: dict[str, str] = {}
    for items in resources.values():
        for obj in _as_list(items):
            _collect_lineage(obj, out)
    return out


def _collect_lineage(node: Any, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        oid = node.get("id")
        if isinstance(oid, str) and _UUID_RE.match(oid):
            lin = node.get("lineage_id")
            out[oid] = str(lin) if lin else oid
        for value in node.values():
            _collect_lineage(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_lineage(value, out)


def _canonicalize(value: Any, lineage_by_id: dict[str, str], *, key: str | None = None) -> Any:
    """Strip identity/volatile keys everywhere and rewrite embedded cross-reference
    UUIDs to lineage space, so two logically-identical objects in different
    environments canonicalize identically."""
    if isinstance(value, dict):
        return {
            k: _canonicalize(v, lineage_by_id, key=k)
            for k, v in value.items()
            if k not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_canonicalize(v, lineage_by_id, key=key) for v in value]
    if key in _REMAP_KEYS and isinstance(value, str) and _UUID_RE.match(value):
        return lineage_by_id.get(value, value)
    return value


def _fingerprint(obj: dict, lineage_by_id: dict[str, str]) -> str:
    canon = _canonicalize(obj, lineage_by_id)
    payload = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_lineage_index(resources: dict[str, Any]) -> dict[str, str]:
    """Public: map every object's local id -> lineage id across a bundle's
    resources (for computing per-object fingerprints, e.g. release_items)."""
    return _lineage_by_id(resources)


def object_fingerprint(obj: dict, lineage_index: dict[str, str]) -> str:
    """Public: the content fingerprint of one exported object (identity/volatile
    keys stripped, embedded refs normalized to lineage space)."""
    return _fingerprint(obj, lineage_index)


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #
def _record_row_count(items: list[dict]) -> int:
    """Total record rows across a ``records`` section (list of per-entity sets)."""
    return sum(len(rs.get("records") or []) for rs in items)


def _count_only_diff(resource_type: str, src_items: list[dict], tgt_items: list[dict]) -> ResourceDiff:
    """Data-layer types: report the source row count as an informational tally.

    A promotion imports these best-effort (records are always inserted; documents
    re-ingest), so a precise added/changed classification is not attempted here."""
    src_count = _record_row_count(src_items) if resource_type == "records" else len(src_items)
    return ResourceDiff(resource_type=resource_type, added=src_count, count_only=True)


def _obj_diff(resource_type: str, obj: dict, status: ObjectStatus) -> ObjectDiff:
    return ObjectDiff(
        resource_type=resource_type,
        status=status,
        lineage_id=str(obj["lineage_id"]) if obj.get("lineage_id") else None,
        natural_key=_natural_key(resource_type, obj),
        name=_name(obj),
    )


def compute_diff(
    source_resources: dict[str, Any],
    target_resources: dict[str, Any],
    *,
    include_data: bool = False,
    manage_deletes_for: set[str] | frozenset[str] | None = None,
) -> BundleDiff:
    """Diff ``source_resources`` (what a release would apply) against
    ``target_resources`` (the target org's current state).

    ``manage_deletes_for`` limits which resource types report ``deleted`` (target
    objects absent from the source); defaults to all config types. Data types are
    count-only unless ``include_data`` (still not correlated per row here)."""
    manage = (
        manage_deletes_for
        if manage_deletes_for is not None
        else (frozenset(RESOURCE_ORDER) - DATA_RESOURCE_TYPES)
    )
    src_lineage = _lineage_by_id(source_resources)
    tgt_lineage = _lineage_by_id(target_resources)

    resources: list[ResourceDiff] = []
    totals = {"added": 0, "changed": 0, "unchanged": 0, "deleted": 0}

    for rtype in RESOURCE_ORDER:
        src_items = _as_list(source_resources.get(rtype))
        tgt_items = _as_list(target_resources.get(rtype))

        if rtype in DATA_RESOURCE_TYPES and not include_data:
            rd = _count_only_diff(rtype, src_items, tgt_items)
            resources.append(rd)
            continue

        rd = ResourceDiff(resource_type=rtype)
        by_lineage, by_natural = _index(rtype, tgt_items)
        matched: set[str] = set()

        for src in src_items:
            tgt = _match_target(rtype, src, by_lineage, by_natural)
            if tgt is None:
                rd.added += 1
                rd.objects.append(_obj_diff(rtype, src, ObjectStatus.ADDED))
                continue
            matched.add(str(tgt.get("id")))
            if _fingerprint(src, src_lineage) == _fingerprint(tgt, tgt_lineage):
                rd.unchanged += 1  # counted, not listed
            else:
                rd.changed += 1
                rd.objects.append(_obj_diff(rtype, src, ObjectStatus.CHANGED))

        if rtype in manage:
            for tgt in tgt_items:
                if str(tgt.get("id")) not in matched:
                    rd.deleted += 1
                    rd.objects.append(_obj_diff(rtype, tgt, ObjectStatus.DELETED))

        resources.append(rd)
        totals["added"] += rd.added
        totals["changed"] += rd.changed
        totals["unchanged"] += rd.unchanged
        totals["deleted"] += rd.deleted

    delete_by_type = {r.resource_type: r.deleted for r in resources}
    delete_order = [t for t in reversed(RESOURCE_ORDER) if delete_by_type.get(t, 0) > 0]
    return BundleDiff(
        resources=resources,
        delete_order=delete_order,
        has_deletes=any(r.deleted > 0 for r in resources),
        totals=totals,
    )
