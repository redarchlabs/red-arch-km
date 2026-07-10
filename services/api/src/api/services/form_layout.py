"""Pure, DB-free validation + traversal for the form element tree.

The service loads entity fields/relationships and hands them here as plain maps,
so this module is trivially unit-testable and is the single source of truth for
"is this layout valid?" — reused by ``FormService`` (create/update) and by the
AI agent form tools. It never touches the database or session.

Entity-context rules as the tree is walked (mirrors the entity relationship
model in ``models/custom_entity.py``):

* Root elements bind to the form's root entity.
* A ``section`` (1:1) relationship is a to-one FK on the root: ``source == root``;
  its children bind to the relationship's ``target``.
* A ``table``/``block`` (1:M) anchor relationship *targets* the root: ``target
  == root``; the child rows/elements bind to the relationship's ``source``.
* A table ``related`` column is a to-one FK on the child (``source == child``);
  the column's field lives on that relationship's ``target``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from api.schemas.form_elements import MAX_TREE_DEPTH, tree_depth


class LayoutError(ValueError):
    """A form layout references a field/relationship that doesn't exist or is
    used in an illegal position."""


@dataclass(frozen=True)
class RelInfo:
    source_id: uuid.UUID
    target_id: uuid.UUID


def collect_relationship_ids(elements: list[Any]) -> set[uuid.UUID]:
    """Every relationship id referenced anywhere in the tree (anchors, sections,
    blocks, and table ``related`` columns) — so the caller can bulk-load them."""
    out: set[uuid.UUID] = set()

    def visit(el: Any) -> None:
        etype = getattr(el, "type", None)
        if etype == "section":
            out.add(el.relationship_id)
            for child in el.elements:
                visit(child)
        elif etype == "block":
            out.add(el.anchor_relationship_id)
            for child in el.elements:
                visit(child)
        elif etype == "table":
            out.add(el.anchor_relationship_id)
            for col in el.columns:
                if getattr(col, "kind", None) == "related":
                    out.add(col.relationship_id)
        elif etype == "tab_group":
            for tab in el.tabs:
                for child in tab.elements:
                    visit(child)
        elif etype == "accordion":
            for pane in el.panes:
                for child in pane.elements:
                    visit(child)
        elif etype == "columns":
            for col in el.columns:
                for child in col.elements:
                    visit(child)
        elif etype == "panel":
            for child in el.elements:
                visit(child)

    for e in elements:
        visit(e)
    return out


def validate(
    elements: list[Any],
    root_entity_id: uuid.UUID,
    fields_by_entity: dict[uuid.UUID, set[str]],
    rels: dict[uuid.UUID, RelInfo],
) -> None:
    """Raise :class:`LayoutError` if the tree references anything invalid.

    ``fields_by_entity`` must contain an entry for the root entity and every
    entity reachable via a referenced relationship. ``rels`` maps each referenced
    relationship id to its ``(source, target)`` entity ids.
    """
    if tree_depth(elements) > MAX_TREE_DEPTH:
        raise LayoutError(f"layout nesting too deep (max {MAX_TREE_DEPTH})")
    _validate_list(elements, root_entity_id, fields_by_entity, rels)


def _entity_fields(entity_id: uuid.UUID, fields_by_entity: dict[uuid.UUID, set[str]]) -> set[str]:
    fields = fields_by_entity.get(entity_id)
    if fields is None:
        raise LayoutError(f"entity {entity_id} not available")
    return fields


def _require_field(slug: str, entity_id: uuid.UUID, fields_by_entity: dict[uuid.UUID, set[str]]) -> None:
    if slug not in _entity_fields(entity_id, fields_by_entity):
        raise LayoutError(f"unknown field {slug!r} on entity {entity_id}")


def _require_rel(rel_id: uuid.UUID, rels: dict[uuid.UUID, RelInfo]) -> RelInfo:
    info = rels.get(rel_id)
    if info is None:
        raise LayoutError(f"unknown relationship {rel_id}")
    return info


def _validate_list(
    elements: list[Any],
    ctx_entity: uuid.UUID,
    fields_by_entity: dict[uuid.UUID, set[str]],
    rels: dict[uuid.UUID, RelInfo],
) -> None:
    for el in elements:
        _validate_element(el, ctx_entity, fields_by_entity, rels)


def _validate_element(
    el: Any,
    ctx: uuid.UUID,
    fields_by_entity: dict[uuid.UUID, set[str]],
    rels: dict[uuid.UUID, RelInfo],
) -> None:
    etype = el.type
    if etype == "field":
        _require_field(el.slug, ctx, fields_by_entity)
    elif etype == "calculated":
        if el.target_slug is not None:
            _require_field(el.target_slug, ctx, fields_by_entity)
    elif etype in ("label", "button", "form_ref", "input", "live_value", "chat"):
        return  # presentational / unbound — nothing to bind at this entity level
    elif etype == "section":
        info = _require_rel(el.relationship_id, rels)
        if info.source_id != ctx:
            raise LayoutError(f"section relationship {el.relationship_id} is not a to-one on this entity")
        _validate_list(el.elements, info.target_id, fields_by_entity, rels)
    elif etype in ("table", "block"):
        info = _require_rel(el.anchor_relationship_id, rels)
        if info.target_id != ctx:
            raise LayoutError(f"{etype} relationship {el.anchor_relationship_id} does not target this entity")
        child = info.source_id
        if etype == "block":
            _validate_list(el.elements, child, fields_by_entity, rels)
        else:
            _validate_table_columns(el.columns, child, fields_by_entity, rels)
    elif etype == "tab_group":
        for tab in el.tabs:
            _validate_list(tab.elements, ctx, fields_by_entity, rels)
    elif etype == "accordion":
        for pane in el.panes:
            _validate_list(pane.elements, ctx, fields_by_entity, rels)
    elif etype == "columns":
        for col in el.columns:
            _validate_list(col.elements, ctx, fields_by_entity, rels)
    elif etype == "panel":
        _validate_list(el.elements, ctx, fields_by_entity, rels)
    else:  # pragma: no cover - the discriminated union forbids other types
        raise LayoutError(f"unknown element type {etype!r}")


# ------------------------------------------------------------------ #
# Binding extraction — flatten the tree into the data it reads/writes.
#
# Layout containers (tab_group/panel/accordion/columns) never change entity
# context, and data containers (section/table/block) only hold leaf children, so
# the data model is exactly: a root binding + a flat list of related-container
# bindings (each relative to the root). Cross-entity table columns add a single
# extra hop captured in ``RelatedColBinding``.
# ------------------------------------------------------------------ #
@dataclass
class CalcBinding:
    target_slug: str
    expression: Any


@dataclass
class RootBinding:
    display_slugs: list[str]  # field elements in order (incl. read-only)
    write_slugs: set[str]  # editable field elements only
    calc: list[CalcBinding]  # calculated elements with a target_slug (persisted)


@dataclass
class SectionBinding:
    kind = "section"
    rel_id: uuid.UUID
    mode: str
    entity_id: uuid.UUID  # the related entity (relationship target)
    display_slugs: list[str]
    write_slugs: set[str]
    calc: list[CalcBinding]


@dataclass
class RelatedColBinding:
    rel_id: uuid.UUID
    entity_id: uuid.UUID  # the related entity (relationship target)
    slug: str
    editable: bool


@dataclass
class TableBinding:
    kind = "table"
    rel_id: uuid.UUID  # anchor (1:M) relationship
    entity_id: uuid.UUID  # the child entity (relationship source)
    display_slugs: list[str]  # anchor field columns (incl. read-only)
    write_slugs: set[str]  # editable anchor field columns
    related_cols: list[RelatedColBinding]


@dataclass
class BlockBinding:
    kind = "block"
    rel_id: uuid.UUID  # anchor (1:M) relationship
    entity_id: uuid.UUID  # the child entity (relationship source)
    display_slugs: list[str]
    write_slugs: set[str]
    calc: list[CalcBinding]


@dataclass
class Bindings:
    root: RootBinding
    containers: list[Any]  # SectionBinding | TableBinding | BlockBinding


def _leaf_slugs(elements: list[Any]) -> tuple[list[str], set[str], list[CalcBinding]]:
    """Collect (display order, writable, calc) from a section/block's children."""
    display: list[str] = []
    write: set[str] = set()
    calc: list[CalcBinding] = []
    for el in elements:
        if el.type == "field":
            display.append(el.slug)
            if not el.read_only:
                write.add(el.slug)
        elif el.type == "calculated" and el.target_slug is not None:
            calc.append(CalcBinding(el.target_slug, el.expression))
    return display, write, calc


def flatten(elements: list[Any], rels: dict[uuid.UUID, RelInfo]) -> Bindings:
    """Flatten a validated tree into its data bindings (see module note)."""
    root = RootBinding(display_slugs=[], write_slugs=set(), calc=[])
    containers: list[Any] = []

    def visit(el: Any) -> None:
        etype = el.type
        if etype == "field":
            root.display_slugs.append(el.slug)
            if not el.read_only:
                root.write_slugs.add(el.slug)
        elif etype == "calculated" and el.target_slug is not None:
            root.calc.append(CalcBinding(el.target_slug, el.expression))
        elif etype == "section":
            info = rels[el.relationship_id]
            display, write, calc = _leaf_slugs(el.elements)
            containers.append(
                SectionBinding(el.relationship_id, el.mode, info.target_id, display, write, calc)
            )
        elif etype == "block":
            info = rels[el.anchor_relationship_id]
            display, write, calc = _leaf_slugs(el.elements)
            containers.append(
                BlockBinding(el.anchor_relationship_id, info.source_id, display, write, calc)
            )
        elif etype == "table":
            info = rels[el.anchor_relationship_id]
            display: list[str] = []
            write: set[str] = set()
            related: list[RelatedColBinding] = []
            for col in el.columns:
                if col.kind == "field":
                    display.append(col.slug)
                    if not col.read_only:
                        write.add(col.slug)
                else:  # related
                    rinfo = rels[col.relationship_id]
                    related.append(
                        RelatedColBinding(col.relationship_id, rinfo.target_id, col.slug, col.editable)
                    )
            containers.append(
                TableBinding(el.anchor_relationship_id, info.source_id, display, write, related)
            )
        elif etype == "tab_group":
            for tab in el.tabs:
                for child in tab.elements:
                    visit(child)
        elif etype == "accordion":
            for pane in el.panes:
                for child in pane.elements:
                    visit(child)
        elif etype == "columns":
            for col in el.columns:
                for child in col.elements:
                    visit(child)
        elif etype == "panel":
            for child in el.elements:
                visit(child)
        # label/button: no data binding

    for e in elements:
        visit(e)
    return Bindings(root=root, containers=containers)


def _validate_table_columns(
    columns: list[Any],
    child_entity: uuid.UUID,
    fields_by_entity: dict[uuid.UUID, set[str]],
    rels: dict[uuid.UUID, RelInfo],
) -> None:
    for col in columns:
        if col.kind == "field":
            _require_field(col.slug, child_entity, fields_by_entity)
        else:  # related
            info = _require_rel(col.relationship_id, rels)
            if info.source_id != child_entity:
                raise LayoutError(
                    f"related column relationship {col.relationship_id} is not a to-one on the table entity"
                )
            _require_field(col.slug, info.target_id, fields_by_entity)
